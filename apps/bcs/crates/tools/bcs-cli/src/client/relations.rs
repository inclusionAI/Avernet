use super::{BcsClient};
use anyhow::{Context, Result};
use bcs_protocol::{
    FriendApiResponse,
    SetVisibilityRequest,
};

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
}
