//! client implementation.
use super::*;
impl BcsClient {
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

pub(super) fn normalize_tags(tags: &[String]) -> Vec<String> {
        tags.iter()
            .map(|tag| tag.trim().to_string())
            .filter(|tag| !tag.is_empty())
            .collect()
    }

/// Submit a chat run without waiting for the response. Returns immediately
    /// with a `run_id` that can be polled via [`Self::chat_run_status`].
    ///
    /// Polling budgets are intentionally not part of this request. The BCS
    /// server owns the run lifecycle independently from how long this CLI
    /// waits for status updates.
    pub async fn chat_async(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        organization_code: Option<&str>,
        client_wait_timeout_ms: u64,
        detach: bool,
    ) -> Result<ChatRunSubmitResponse, ChatAsyncError> {
        let url = format!("{}/bots/{}/chat-async", self.base_url, bot_id);
        let payload = Self::chat_async_payload(
            message,
            from,
            session_id,
            tags,
            response_mode,
            organization_code,
        );

        let response = self
            .add_chat_dispatch_headers(
                self.http_client
                    .post(&url)
                    .json(&payload)
                    .timeout(Duration::from_secs(10)),
                detach,
                client_wait_timeout_ms,
            )
            .send()
            .await
            .map_err(|err| ChatAsyncError::Transport(err.to_string()))?;

        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body = response.text().await.unwrap_or_default();
            return Err(ChatAsyncError::NotSuccessful { status, body });
        }

        let submit: ChatRunSubmitResponse = response
            .json()
            .await
            .map_err(|err| ChatAsyncError::InvalidResponse(err.to_string()))?;
        Ok(submit)
    }

pub(super) fn chat_async_payload(
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        organization_code: Option<&str>,
    ) -> serde_json::Value {
        let mut payload = serde_json::json!({
            "message": message,
            "from": from,
        });
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

/// Poll a chat run until it reaches a terminal state (sync mode).
    ///
    /// `submit` is the response from a successful `chat_async` call — its
    /// `run_id` is polled and its `session_id`/`bot_uuid` fill branches where
    /// no status response is available (local timeout before first status).
    /// `poll_wait_ms` controls each long-poll HTTP hop.
    /// `overall_timeout` caps the total wall-clock spent polling; on expiry
    /// the result carries `state="timeout"` with IDs from `submit` and the
    /// server-side run keeps executing.
    ///
    /// Returns `Ok(ChatRunOutcome)` on every path — terminal, timeout, and
    /// poll errors are all structured outcomes, never bare `Err`.
    pub async fn chat_poll_run(
        &self,
        submit: &ChatRunSubmitResponse,
        poll_wait_ms: u64,
        overall_timeout: Duration,
    ) -> Result<ChatRunOutcome> {
        let deadline = tokio::time::Instant::now() + overall_timeout;
        let mut since_version: u64 = 0;
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                warn!(
                    run_id = %submit.run_id,
                    "chat_poll_run: local polling timeout; run left active"
                );
                return Ok(Self::timeout_outcome(submit, overall_timeout));
            }
            let wait_ms = remaining.as_millis().min(poll_wait_ms as u128) as u64;
            match tokio::time::timeout(remaining,
                self.chat_run_status(&submit.run_id, Some(since_version), Some(wait_ms))).await
            {
                Ok(Ok(status)) => {
                    since_version = status.version;
                    if status.is_terminal() {
                        return Ok(Self::terminal_outcome(&status));
                    }
                }
                Ok(Err(err)) => {
                    return Ok(Self::poll_error_outcome(submit, &err.to_string()));
                }
                Err(_) => return Ok(Self::timeout_outcome(submit, overall_timeout)),
            }
        }
    }

/// Poll a chat run until BCS reports it as `running` (detach mode).
    /// Accepts `Running` or `Completed` as success — mirrors the legacy
    /// `chat_polling_detach` that treated `Completed | Running` as detach-ack
    /// (a run may complete before the first status response arrives).
    ///
    /// Same signature contract as [`Self::chat_poll_run`]: `submit` metadata
    /// fills ID-bearing branches that lack a status response.
    pub async fn chat_poll_run_until_running(
        &self,
        submit: &ChatRunSubmitResponse,
        poll_wait_ms: u64,
        overall_timeout: Duration,
    ) -> Result<ChatRunOutcome> {
        let deadline = tokio::time::Instant::now() + overall_timeout;
        let mut since_version: u64 = 0;
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                warn!(
                    run_id = %submit.run_id,
                    "chat_poll_run_until_running: local timeout before detach ack"
                );
                return Ok(Self::timeout_outcome(submit, overall_timeout));
            }
            let wait_ms = remaining.as_millis().min(poll_wait_ms as u128) as u64;
            match tokio::time::timeout(remaining,
                self.chat_run_status(&submit.run_id, Some(since_version), Some(wait_ms))).await
            {
                Ok(Ok(status)) => {
                    match status.state {
                        ChatRunState::Completed | ChatRunState::Running => {
                            return Ok(Self::running_outcome(&status));
                        }
                        ChatRunState::Failed | ChatRunState::Cancelled => {
                            return Ok(Self::terminal_outcome(&status));
                        }
                        ChatRunState::Pending | ChatRunState::Submitted
                        | ChatRunState::Unknown => {
                            if status.is_terminal() {
                                return Ok(Self::terminal_outcome(&status));
                            }
                        }
                    }
                    since_version = status.version;
                }
                Ok(Err(err)) => {
                    return Ok(Self::poll_error_outcome(submit, &err.to_string()));
                }
                Err(_) => return Ok(Self::timeout_outcome(submit, overall_timeout)),
            }
        }
    }

/// Terminal outcome from a status response. `delivered` is true only for
    /// `Completed`; `Failed`/`Cancelled`/terminal-`Unknown` yield
    /// `delivered=false`. IDs come from the status response (the run has
    /// been created and a terminal status was received).
    pub(super) fn terminal_outcome(status: &ChatRunStatusResponse) -> ChatRunOutcome {
        let delivered = matches!(status.state, ChatRunState::Completed);
        ChatRunOutcome {
            delivery: status.delivery.clone(),
            delivered,
            submitted: true,
            run_id: Some(status.run_id.clone()),
            session_id: Some(status.session_id.clone()),
            bot_uuid: Some(status.bot_uuid.clone()),
            state: status.state.as_str().to_string(),
            response_content: Some(status.response.content.clone()),
            error_message: status.error_message.clone(),
            content_truncated: status.content_truncated,
        }
    }

/// Detach success outcome: `delivered=true`, no response content (detach
    /// does not wait for completion). State is `running` or `completed`. IDs
    /// come from the status response (the run has been created and a status
    /// response was received — unlike timeout/poll_error which use submit).
    pub(super) fn running_outcome(status: &ChatRunStatusResponse) -> ChatRunOutcome {
        ChatRunOutcome {
            delivery: status.delivery.clone(),
            delivered: true,
            submitted: true,
            run_id: Some(status.run_id.clone()),
            session_id: Some(status.session_id.clone()),
            bot_uuid: Some(status.bot_uuid.clone()),
            state: status.state.as_str().to_string(),
            response_content: None,
            error_message: None,
            content_truncated: false,
        }
    }

/// Local polling deadline hit. IDs come from the submit response so the
    /// outcome is non-empty even with a zero-length timeout before any status
    /// response arrived.
    pub(super) fn timeout_outcome(
        submit: &ChatRunSubmitResponse,
        overall_timeout: Duration,
    ) -> ChatRunOutcome {
        ChatRunOutcome {
            delivery: submit.delivery.clone(),
            delivered: false,
            submitted: true,
            run_id: Some(submit.run_id.clone()),
            session_id: Some(submit.session_id.clone()),
            bot_uuid: Some(submit.bot_uuid.clone()),
            state: "timeout".to_string(),
            response_content: None,
            error_message: Some(format!(
                "local polling timeout after {} ms; run {} is still active",
                overall_timeout.as_millis(),
                submit.run_id
            )),
            content_truncated: false,
        }
    }

/// `chat_run_status` error during polling. IDs come from the submit
    /// response (already known); the error is preserved in `error_message`.
    pub(super) fn poll_error_outcome(submit: &ChatRunSubmitResponse, error: &str) -> ChatRunOutcome {
        ChatRunOutcome {
            delivery: submit.delivery.clone(),
            delivered: false,
            submitted: true,
            run_id: Some(submit.run_id.clone()),
            session_id: Some(submit.session_id.clone()),
            bot_uuid: Some(submit.bot_uuid.clone()),
            state: "poll_error".to_string(),
            response_content: None,
            error_message: Some(format!("chat_run_status failed: {}", error)),
            content_truncated: false,
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

/// Validate an authoring-time state-machine YAML document against the
    /// currently running BCS server.
    pub async fn validate_collaboration_definition(
        &self,
        definition_yaml: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/collaboration/definitions/validate", self.base_url);
        let response = self
            .add_auth(self.http_client.post(&url).json(&serde_json::json!({
                "definition_yaml": definition_yaml,
            })))
            .send()
            .await
            .context("Failed to validate collaboration definition YAML")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Validate collaboration definition YAML failed ({}): {}",
                status,
                body
            ));
        }

        response
            .json()
            .await
            .context("Invalid collaboration definition validation response")
    }

pub async fn get_session_state_machine_permission(
        &self,
        session_id: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/state-machine-permission",
            self.base_url, session_id
        );
        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to query session state-machine permission")?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Query session state-machine permission failed ({}): {}",
                status,
                body
            ));
        }
        response
            .json()
            .await
            .context("Invalid session state-machine permission response")
    }

pub async fn run_session_collaboration(
        &self,
        options: RunSessionCollaborationOptions,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/state-machine-runs",
            self.base_url, options.session_id
        );
        let mut payload = serde_json::json!({
            "definition_yaml": options.definition_yaml,
            "participant_bindings": options.participant_bindings,
            "input": options.input,
        });
        if let Some(opening_message) = options.opening_message {
            payload["opening_message"] = opening_message;
        }
        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to start session state-machine run")?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Start session state-machine run failed ({}): {}",
                status,
                body
            ));
        }
        response
            .json()
            .await
            .context("Invalid session state-machine run response")
    }

pub(super) fn collaboration_run_url(&self, run_id: &str, suffix: &[&str]) -> Result<reqwest::Url> {
        let mut url = reqwest::Url::parse(&self.base_url)?;
        url.path_segments_mut().map_err(|_| anyhow!("BCS URL cannot contain path segments"))?
            .pop_if_empty().push("state-machine-runs").push(run_id).extend(suffix.iter().copied());
        Ok(url)
    }

pub async fn query_collaboration_run(&self, run_id: &str, suffix: &[&str]) -> Result<serde_json::Value> {
        let response = self.add_auth(self.http_client.get(self.collaboration_run_url(run_id, suffix)?))
            .send().await.context("Failed to query state-machine Run")?;
        Self::collaboration_response(response).await
    }

pub async fn respond_collaboration_node(&self, run_id: &str, node_id: &str, content: &str) -> Result<serde_json::Value> {
        if content.trim().is_empty() { return Err(anyhow!("Human response must not be empty")); }
        let pending = self.query_collaboration_run(run_id, &["pending-human-nodes"]).await?;
        let matches = pending.as_array().is_some_and(|nodes| nodes.iter().any(|node|
            node.get("node_id").and_then(serde_json::Value::as_str) == Some(node_id)));
        if !matches { return Err(anyhow!("Execution node is not pending for the authenticated Human; query --pending again")); }
        let response = self.add_auth(self.http_client.post(self.collaboration_run_url(run_id, &["nodes", node_id, "respond"])?))
            .json(&serde_json::json!({"content": content})).send().await.context("Failed to respond to Human node")?;
        Self::collaboration_response(response).await
    }

pub(super) async fn collaboration_response(response: reqwest::Response) -> Result<serde_json::Value> {
        if !response.status().is_success() {
            let status = response.status();
            return Err(anyhow!("State-machine request failed ({status}): {}", response.text().await?));
        }
        let value: serde_json::Value = response.json().await.context("Invalid state-machine response")?;
        Ok(value.get("data").cloned().unwrap_or(value))
    }

/// Create a state-machine group from authoring YAML and logical participant bindings.
    pub async fn create_custom_group(
        &self,
        options: CreateCustomGroupOptions,
    ) -> Result<CreateGroupResponse> {
        self.create_custom_group_with_initial_session(options, true)
            .await
    }
}
