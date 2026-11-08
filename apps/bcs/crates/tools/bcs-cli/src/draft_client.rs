use super::BcsClient;
use serde_json::Value;

#[derive(Debug, thiserror::Error)]
#[error("draft request failed{status_text}", status_text = .status.map(|v| format!(" (HTTP {v})")).unwrap_or_default())]
pub struct DraftRequestError {
    pub status: Option<u16>,
}

impl BcsClient {
    /// Fixed paths only; body/response payloads are never included in errors or logs.
    pub async fn draft_request(
        &self,
        method: reqwest::Method,
        suffix: &str,
        body: Option<&Value>,
    ) -> Result<Value, DraftRequestError> {
        let url = format!(
            "{}/collaboration/drafts/{}",
            self.base_url.trim_end_matches('/'),
            suffix
        );
        let mut request = self.add_headers(self.http_client.request(method, url));
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .map_err(|_| DraftRequestError { status: None })?;
        let status = response.status();
        if !status.is_success() {
            return Err(DraftRequestError {
                status: Some(status.as_u16()),
            });
        }
        if status == reqwest::StatusCode::NO_CONTENT {
            return Ok(Value::Null);
        }
        response
            .json()
            .await
            .map_err(|_| DraftRequestError { status: None })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collaboration_draft::{DraftCommands, execute};
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::time::Duration;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{method, path},
    };

    #[tokio::test]
    async fn draft_timeout_recovers_next_revision_without_reposting() {
        let server = MockServer::start().await;
        let id = "01010101-0202-0303-0404-050505050505";
        Mock::given(method("POST"))
            .and(path(format!("/collaboration/drafts/{id}/revisions")))
            .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(5)))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path(format!("/collaboration/drafts/{id}/revisions/2")))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({
                "draft_id":id,"revision":2,"definition_yaml":"new","title":null,
                "content_hash":hex::encode(Sha256::digest(b"new")),"size_bytes":3
            })))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("POST"))
            .and(path(format!(
                "/collaboration/drafts/{id}/revisions/2/validate"
            )))
            .respond_with(ResponseTemplate::new(503))
            .expect(1)
            .mount(&server)
            .await;
        let mut client = BcsClient::new(server.uri());
        client.http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
            .unwrap();
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("input.yaml");
        std::fs::write(&file, "new").unwrap();
        let (result, ok) = execute(
            &client,
            DraftCommands::Update {
                draft_id: id.into(),
                file,
                expected_revision: 1,
                title: None,
            },
        )
        .await;
        assert!(!ok);
        assert_eq!(result["saved"], true);
        assert_eq!(result["matched_existing_revision"], true);
        assert_eq!(result["phase"], "validate");
        assert!(result.get("message").is_none());
    }
}
