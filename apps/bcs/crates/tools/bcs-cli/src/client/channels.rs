use super::{BcsClient};
use anyhow::{Context, Result, anyhow};

impl BcsClient {
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
            return Err(anyhow!(
                "Create channel binding failed ({}): {}",
                status,
                body
            ));
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
            return Err(anyhow!(
                "List channel bindings failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid list channel bindings response")?;
        Ok(result)
    }

    /// List channel conversation mappings for a BCS session.
    /// `GET /channels/conversations/by-session`
    pub async fn list_channel_conversations_by_session(
        &self,
        bcs_session_id: &str,
        channel_type: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/channels/conversations/by-session", self.base_url);
        let response = self
            .add_auth(self.http_client.get(&url).query(&[
                ("bcs_session_id", bcs_session_id),
                ("channel_type", channel_type),
            ]))
            .send()
            .await
            .context("Failed to list channel conversations by session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "List channel conversations by session failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid channel conversation list response")?;
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
            return Err(anyhow!(
                "Delete channel binding failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid delete channel binding response")?;
        Ok(result)
    }
}

#[cfg(test)]
mod tests;
