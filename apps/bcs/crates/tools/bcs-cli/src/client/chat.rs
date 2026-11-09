use super::{BcsClient, ChatAsyncError, ChatRunOutcome};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    ChatRunCancelResponse,
    ChatRunState,
    ChatRunStatusResponse,
    ChatRunSubmitResponse,
};
use tracing::{debug, warn};
use std::time::Duration;

impl BcsClient {
    fn normalize_tags(tags: &[String]) -> Vec<String> {
        tags.iter()
            .map(|tag| tag.trim().to_string())
            .filter(|tag| !tag.is_empty())
            .collect()
    }

    // ========================================================================
    // Async chat run flow (submit + long-poll + cancel)
    // ========================================================================

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
        // `bot_id` is one URL path segment. Composite IDs such as
        // `default:197262` must encode the colon before the request reaches
        // gateways that apply strict path matching.
        let encoded_bot_id = urlencoding::encode(bot_id);
        let url = format!("{}/bots/{}/chat-async", self.base_url, encoded_bot_id);
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

    fn chat_async_payload(
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
        if let Some(code) = organization_code
            .map(str::trim)
            .filter(|code| !code.is_empty())
        {
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
                        ChatRunState::Pending | ChatRunState::Submitted | ChatRunState::Unknown => {
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

    // ------------------------------------------------------------------
    // Internal helpers for building ChatRunOutcome from known state
    // ------------------------------------------------------------------

    /// Terminal outcome from a status response. `delivered` is true only for
    /// `Completed`; `Failed`/`Cancelled`/terminal-`Unknown` yield
    /// `delivered=false`. IDs come from the status response (the run has
    /// been created and a terminal status was received).
    fn terminal_outcome(status: &ChatRunStatusResponse) -> ChatRunOutcome {
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
    fn running_outcome(status: &ChatRunStatusResponse) -> ChatRunOutcome {
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
    fn timeout_outcome(
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
    fn poll_error_outcome(submit: &ChatRunSubmitResponse, error: &str) -> ChatRunOutcome {
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

    pub fn admitted_outcome(submit: &ChatRunSubmitResponse) -> ChatRunOutcome {
        let state = submit.status.as_deref().unwrap_or("pending");
        ChatRunOutcome {
            delivery: submit.delivery.clone(), delivered: matches!(state, "running" | "completed"),
            submitted: true, run_id: Some(submit.run_id.clone()), session_id: Some(submit.session_id.clone()),
            bot_uuid: Some(submit.bot_uuid.clone()), state: state.into(), response_content: None,
            error_message: None, content_truncated: false,
        }
    }
}

#[cfg(test)]
mod tests;
