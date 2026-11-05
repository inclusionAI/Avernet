use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    FusionRequest,
    FusionResponse,
};

impl BcsClient {
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
