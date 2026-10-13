use std::io;

use async_trait::async_trait;
use clisdk::{AuthEnv, Client as CliHttpClient, create_cli_http_client};
use reqwest::Method;
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};

use crate::api::GoalApi;
use crate::error::AppError;
use crate::models::{Envelope, TaskIntakeCommand, TaskIntakeResult, TaskOpResult};

const INTAKE_PATH: &str = "/api/v1/collaboration/tasks/intake";
const START_PATH: &str = "/api/v1/collaboration/tasks/start";
const TASK_PATH: &str = "/api/v1/collaboration/tasks";
const SESSION_HEADER: &str = "X-Task-Intake-Session-ID";

pub struct ApiClient {
    http_client: CliHttpClient,
}

impl ApiClient {
    pub async fn new() -> Result<Self, AppError> {
        let http_client = create_cli_http_client(AuthEnv {
            client_id: "avernet-goal-cli".to_string(),
            ..Default::default()
        })
        .await
        .map_err(|error| AppError::Unavailable(error.to_string()))?;
        Ok(Self { http_client })
    }

    async fn post<B, T>(
        &self,
        path: &str,
        body: &B,
        session_id: Option<&str>,
    ) -> Result<T, AppError>
    where
        B: Serialize + Sync,
        T: DeserializeOwned,
    {
        let mut request = self
            .http_client
            .new_request(Method::POST, path.to_string())
            .await
            .map_err(|error| AppError::Unavailable(error.to_string()))?
            .json(body);
        if let Some(value) = session_id {
            request = request.header(SESSION_HEADER, value);
        }
        self.execute(request).await
    }

    async fn get<T>(&self, path: &str, query: &[(&str, &str)]) -> Result<T, AppError>
    where
        T: DeserializeOwned,
    {
        let request = self
            .http_client
            .new_request(Method::GET, path.to_string())
            .await
            .map_err(|error| AppError::Unavailable(error.to_string()))?
            .query(query);
        self.execute(request).await
    }

    async fn execute<T>(&self, request: reqwest::RequestBuilder) -> Result<T, AppError>
    where
        T: DeserializeOwned,
    {
        let response = self
            .http_client
            .execute(request)
            .await
            .map_err(|error| AppError::Unavailable(error.to_string()))?;
        let status = response.status();
        let body = response
            .text()
            .await
            .map_err(|error| AppError::Unavailable(error.to_string()))?;

        if status.as_u16() == 401 || status.as_u16() == 403 {
            return Err(AppError::Unauthorized(extract_error_message(&body)));
        }
        if status.is_server_error() {
            return Err(AppError::Unavailable(extract_error_message(&body)));
        }
        if !status.is_success() {
            return Err(AppError::Remote(extract_error_message(&body)));
        }

        let envelope: Envelope<T> = serde_json::from_str(&body).map_err(|error| {
            AppError::Serialization(format!("invalid Gateway response: {error}"))
        })?;
        envelope.data.ok_or_else(|| {
            AppError::Remote(format!(
                "Gateway returned no data (code={}, request_id={}): {}",
                envelope.code, envelope.request_id, envelope.message
            ))
        })
    }

    pub async fn close(&self) -> Result<(), AppError> {
        self.http_client
            .close()
            .await
            .map_err(|error| AppError::Unavailable(error.to_string()))
    }
}

#[async_trait(?Send)]
impl GoalApi for ApiClient {
    async fn intake(
        &self,
        command: &TaskIntakeCommand,
        session_id: Option<&str>,
    ) -> Result<TaskIntakeResult, AppError> {
        self.post(INTAKE_PATH, command, session_id).await
    }

    async fn start(&self, session_id: &str) -> Result<TaskOpResult, AppError> {
        self.post(START_PATH, &json!({ "session_id": session_id }), None)
            .await
    }

    async fn get_task(&self, task_id: &str) -> Result<Value, AppError> {
        let path = format!("{TASK_PATH}/{}", task_id.trim());
        self.get(&path, &[]).await
    }
}

fn extract_error_message(body: &str) -> String {
    let parsed = serde_json::from_str::<Value>(body).ok();
    parsed
        .as_ref()
        .and_then(|value| value.get("message"))
        .and_then(Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .unwrap_or_else(|| {
            if body.trim().is_empty() {
                "request failed"
            } else {
                body
            }
        })
        .to_string()
}

#[allow(dead_code)]
fn _io_error(message: impl Into<String>) -> io::Error {
    io::Error::other(message.into())
}
