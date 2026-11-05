use super::{BcsClient, CreateCustomGroupOptions, RunSessionCollaborationOptions};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    CreateGroupRequest,
    CreateGroupResponse,
};

impl BcsClient {
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

    fn collaboration_run_url(&self, run_id: &str, suffix: &[&str]) -> Result<reqwest::Url> {
        let mut url = reqwest::Url::parse(&self.base_url)?;
        url.path_segments_mut()
            .map_err(|_| anyhow!("BCS URL cannot contain path segments"))?
            .pop_if_empty()
            .push("state-machine-runs")
            .push(run_id)
            .extend(suffix.iter().copied());
        Ok(url)
    }

    pub async fn query_collaboration_run(
        &self,
        run_id: &str,
        suffix: &[&str],
    ) -> Result<serde_json::Value> {
        let response = self
            .add_auth(
                self.http_client
                    .get(self.collaboration_run_url(run_id, suffix)?),
            )
            .send()
            .await
            .context("Failed to query state-machine Run")?;
        Self::collaboration_response(response).await
    }

    pub async fn respond_collaboration_node(
        &self,
        run_id: &str,
        node_id: &str,
        content: &str,
    ) -> Result<serde_json::Value> {
        if content.trim().is_empty() {
            return Err(anyhow!("Human response must not be empty"));
        }
        let pending = self
            .query_collaboration_run(run_id, &["pending-human-nodes"])
            .await?;
        let matches = pending.as_array().is_some_and(|nodes| {
            nodes.iter().any(|node| {
                node.get("node_id").and_then(serde_json::Value::as_str) == Some(node_id)
            })
        });
        if !matches {
            return Err(anyhow!(
                "Execution node is not pending for the authenticated Human; query --pending again"
            ));
        }
        let response = self
            .add_auth(
                self.http_client
                    .post(self.collaboration_run_url(run_id, &["nodes", node_id, "respond"])?),
            )
            .json(&serde_json::json!({"content": content}))
            .send()
            .await
            .context("Failed to respond to Human node")?;
        Self::collaboration_response(response).await
    }

    async fn collaboration_response(response: reqwest::Response) -> Result<serde_json::Value> {
        if !response.status().is_success() {
            let status = response.status();
            return Err(anyhow!(
                "State-machine request failed ({status}): {}",
                response.text().await?
            ));
        }
        let value: serde_json::Value = response
            .json()
            .await
            .context("Invalid state-machine response")?;
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

    /// Configure a state-machine group with an explicit initial-session choice.
    pub async fn create_custom_group_with_initial_session(
        &self,
        options: CreateCustomGroupOptions,
        create_initial_session: bool,
    ) -> Result<CreateGroupResponse> {
        let url = format!("{}/groups", self.base_url);
        let payload = CreateGroupRequest {
            retired_service_spec: (),
            human_mention_notify_mode: Default::default(),
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
}
