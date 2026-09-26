//! client implementation.
use super::*;
impl BcsClient {
/// Configure a state-machine group with an explicit initial-session choice.
    pub async fn create_custom_group_with_initial_session(
        &self,
        options: CreateCustomGroupOptions,
        create_initial_session: bool,
    ) -> Result<CreateGroupResponse> {
        let url = format!("{}/groups", self.base_url);
        let payload = CreateGroupRequest {
            create_initial_session,
            id: options.id,
            label: None,
            mode: None,
            driver_bot: Some(options.driver_bot.clone()),
            participants: Vec::new(),
            participant_bindings: options.participant_bindings,
            target_actor_id: None,
            routing_policy: None,
            context: options.context,
            opening_message: None,
            topic: options.topic,
            group_kind: None,
            service_spec: None,
            group_strategy: Some("state_machine".to_string()),
            originator: Some(options.driver_bot),
            collaboration_definition_yaml: Some(options.definition_yaml),
            auto_start_on_service_invocation: Some(options.auto_start_on_service_invocation),
            start_initial_run: None,
            visibility: None,
            event_subscriptions: Vec::new(),
        };
        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to create custom collaboration group")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Create custom collaboration group failed ({}): {}",
                status,
                body
            ));
        }

        response
            .json()
            .await
            .context("Invalid custom collaboration group response")
    }

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
            service_spec: None,
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
        self.create_group_with_strategy_and_context(
            driver_bot,
            participants,
            context,
            topic,
            None,
        )
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
            service_spec: None,
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
                (
                    "include_session_groups",
                    include_session_groups.to_string(),
                ),
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
            .add_auth(self.http_client.get(&url).query(&[
                ("offset", offset.to_string()),
                ("limit", limit.to_string()),
            ]))
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
}
