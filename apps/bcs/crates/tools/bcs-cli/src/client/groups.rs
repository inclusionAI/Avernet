use super::{BcsClient, BotGroupListPage, CurrentActorGroupListPage};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    CreateGroupRequest,
    CreateGroupResponse,
    ParticipantInfo,
};
use tracing::{debug};

impl BcsClient {
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
            retired_service_spec: (),
            human_mention_notify_mode: Default::default(),
            create_initial_session: true,
            mode: Some(mode.to_string()),
            driver_bot: Some(driver_bot.to_string()),
            participants,
            participant_bindings: Default::default(),
            target_actor_id: None,
            id: None,
            label: None,
            routing_policy: None,
            context: None,
            opening_message: None,
            topic: None,
            group_kind: None,
            group_strategy: None,
            originator: None,
            collaboration_definition_yaml: None,
            auto_start_on_service_invocation: None,
            start_initial_run: None,
            visibility: None,
            event_subscriptions: Vec::new(),
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
        self.create_group_with_context(driver_bot, participants, None, None)
            .await
    }

    /// Create a group with optional context and topic.
    pub async fn create_group_with_context(
        &self,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
        context: Option<&str>,
        topic: Option<&str>,
    ) -> Result<CreateGroupResponse> {
        self.create_group_with_strategy_and_context(driver_bot, participants, context, topic, None)
            .await
    }

    /// Create a group with optional context, topic, and group strategy.
    pub async fn create_group_with_strategy_and_context(
        &self,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
        context: Option<&str>,
        topic: Option<&str>,
        group_strategy: Option<&str>,
    ) -> Result<CreateGroupResponse> {
        self.create_group_with_initial_session(
            driver_bot,
            participants,
            context,
            topic,
            group_strategy,
            true,
        )
        .await
    }

    /// Create a Chat/ManagerWorker group with an explicit initial-session choice.
    /// A server supporting this option is required when it is false.
    pub async fn create_group_with_initial_session(
        &self,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
        context: Option<&str>,
        topic: Option<&str>,
        group_strategy: Option<&str>,
        create_initial_session: bool,
    ) -> Result<CreateGroupResponse> {
        let url = format!("{}/groups", self.base_url);

        let payload = CreateGroupRequest {
            retired_service_spec: (),
            human_mention_notify_mode: Default::default(),
            create_initial_session,
            mode: None,
            driver_bot: Some(driver_bot.to_string()),
            participants,
            participant_bindings: Default::default(),
            target_actor_id: None,
            id: None,
            label: None,
            routing_policy: None,
            context: context.map(|s| s.to_string()),
            opening_message: None,
            topic: topic.map(|s| s.to_string()),
            group_kind: None,
            group_strategy: group_strategy.map(str::to_string),
            originator: None,
            collaboration_definition_yaml: None,
            auto_start_on_service_invocation: None,
            start_initial_run: None,
            visibility: None,
            event_subscriptions: Vec::new(),
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
    pub async fn list_bot_groups(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
        include_session_groups: bool,
    ) -> Result<BotGroupListPage> {
        let url = format!(
            "{}/bots/{}/groups",
            self.base_url,
            urlencoding::encode(bot_uuid)
        );

        let response = self
            .add_auth(self.http_client.get(&url).query(&[
                ("offset", offset.to_string()),
                ("limit", limit.to_string()),
                ("include_session_groups", include_session_groups.to_string()),
            ]))
            .send()
            .await
            .context("Failed to list bot groups")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List bot groups failed ({}): {}", status, body));
        }

        response.json().await.context("Invalid bot groups response")
    }

    /// List groups that include the authenticated human or bot actor.
    pub async fn list_my_groups(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<CurrentActorGroupListPage> {
        let url = format!("{}/groups/my", self.base_url);

        let response = self
            .add_auth(
                self.http_client
                    .get(&url)
                    .query(&[("offset", offset.to_string()), ("limit", limit.to_string())]),
            )
            .send()
            .await
            .context("Failed to list current actor groups")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "List current actor groups failed ({}): {}",
                status,
                body
            ));
        }

        response
            .json()
            .await
            .context("Invalid current actor groups response")
    }

    /// Add a member to a group.
    pub async fn add_group_member(
        &self,
        group_id: &str,
        bot_id: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/members", self.base_url, group_id);

        let payload = serde_json::json!({
            "bot_uuid": bot_id
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
}
