use super::{BcsClient};
use anyhow::{Context, Result, anyhow};

impl BcsClient {
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
        group_context_delivery: Option<&str>,
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
        if let Some(delivery) = group_context_delivery {
            payload.insert(
                "group_context_delivery".to_string(),
                serde_json::json!(delivery),
            );
        }

        let response = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(payload)),
            )
            .send()
            .await
            .context("Failed to create session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create session failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid create session response")?;
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid list sessions response")?;
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid get session response")?;
        Ok(result)
    }

    /// Send a chat message into a session.
    /// `POST /sessions/{sid}/chat`. Caller is resolved from the bearer token.
    pub async fn session_chat(&self, sid: &str, message: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/chat",
            self.base_url,
            urlencoding::encode(sid)
        );
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid session chat response")?;
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

        let mut url = format!(
            "{}/sessions/{}/messages",
            self.base_url,
            urlencoding::encode(sid)
        );
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
            return Err(anyhow!(
                "Get session messages failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid session messages response")?;
        Ok(result)
    }

    /// Update session title.
    /// `PATCH /sessions/{sid}`
    pub async fn patch_session(&self, sid: &str, title: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}", self.base_url, urlencoding::encode(sid));
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid patch session response")?;
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid complete session response")?;
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid add session member response")?;
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
            return Err(anyhow!(
                "Remove session member failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid remove session member response")?;
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid set member mode response")?;
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
}

#[cfg(test)]
mod tests;
