use super::{BcsClient};
use anyhow::{Context, Result, anyhow};

impl BcsClient {
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

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid service invoke response")?;
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
            return Err(anyhow!(
                "Service session status failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid service session response")?;
        Ok(result)
    }
}
