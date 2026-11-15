use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    BindingChannels,
    BotCapabilities,
    BotConnectParams,
    BotConnectResponse,
    BotDynamicStatus,
    BotInfo,
    DiscoverBotsExtendedResponse,
    DiscoverBotsResponse,
    JoinRequest,
    JoinResponse,
    OnboardRequest,
    OnboardResponse,
    QueryBotEntry,
    QueryBotsRequest,
    Skill,
    UpdateStatusRequest,
    UpdateStatusResponse,
};
use tracing::{debug, info};

impl BcsClient {
    pub async fn current_bot_uuid(&self) -> Result<String> {
        let base = self.base_url.trim_end_matches('/');
        let base = base.strip_suffix("/api/v1/collaboration")
            .or_else(|| base.strip_suffix("/openapi/v1/collaboration")).unwrap_or(base);
        let url = format!("{base}/api/v1/collaboration/bots/me");
        // This parent/self passthrough endpoint consumes one Bot credential.
        // Office OAuth is used on business requests; do not substitute it here.
        let request = if self.token.is_some() {
            let mut identity = self.clone();
            identity.oauth_headers = None;
            identity.add_headers(self.http_client.get(url))
        } else { self.add_auth(self.http_client.get(url)) };
        let response = request.send().await?;
        if !response.status().is_success() { return Err(anyhow!("Current Bot identity request failed")); }
        let body: serde_json::Value = response.json().await?;
        if body["code"] != 20000 { return Err(anyhow!("Current Bot identity was rejected")); }
        body["data"]["bot"]["bot_id"].as_str().filter(|id| !id.is_empty())
            .map(str::to_owned).context("No authenticated Bot; specify --bot-uuid")
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

    /// Call a Group Context endpoint using BCN query-string context.
    ///
    /// The CLI follows the same direct-BCS auth path as `bcs-cli chat`: the
    /// request carries the normal bot token/OAuth headers and the server
    /// resolves the effective actor from trusted headers. The actor id is not
    /// sent in either the URL query or JSON body.
    pub async fn group_context_request(
        &self,
        operation: &str,
        query: &[(&str, &str)],
        body: serde_json::Value,
    ) -> Result<serde_json::Value> {
        let mut url = reqwest::Url::parse(&format!("{}/groupcontext/{}", self.base_url, operation))
            .context("invalid BCS URL")?;
        {
            let mut pairs = url.query_pairs_mut();
            for (key, value) in query {
                pairs.append_pair(key, value);
            }
        }
        let response = self
            .add_auth(self.http_client.post(url).json(&body))
            .send()
            .await
            .with_context(|| format!("Failed to call group context {}", operation))?;
        Self::ensure_success(response, &format!("Group Context {}", operation)).await
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
        skills: &[String],
        visibility: Option<&str>,
        collaborate_bot: Option<&str>,
        organization_code: Option<&str>,
        role: Option<&str>,
    ) -> Result<DiscoverBotsExtendedResponse> {
        let mut params: Vec<String> = Vec::new();
        if let Some(q) = query {
            params.push(format!("q={}", urlencoding::encode(q)));
        }
        for skill in skills {
            params.push(format!("skill={}", urlencoding::encode(skill)));
        }
        if let Some(vis) = visibility {
            params.push(format!("visibility={}", urlencoding::encode(vis)));
        }
        if let Some(collab) = collaborate_bot {
            params.push(format!("collaborate_bot={}", urlencoding::encode(collab)));
        }
        if let Some(code) = organization_code
            .map(str::trim)
            .filter(|code| !code.is_empty())
        {
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
}

#[cfg(test)]
mod tests;
