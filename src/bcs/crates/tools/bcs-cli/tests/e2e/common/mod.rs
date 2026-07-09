//! E2E test utilities for bcs-cli
//!
//! Provides shared helpers for:
//! - Starting mock BCS servers
//! - Creating temporary session files
//! - Running CLI commands with assertions

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Output;

use assert_cmd::Command;
use serde::{Deserialize, Serialize};
use tempfile::TempDir;
use wiremock::{
    matchers::{method, path},
    Mock, MockServer, ResponseTemplate,
};

/// Session info for mock authentication
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MockSession {
    pub bot_uuid: String,
    pub token: String,
    pub bcs_url: String,
    pub api_base_url: Option<String>,
}

impl MockSession {
    /// Create a mock session with generated values
    pub fn new(server_url: &str) -> Self {
        Self {
            bot_uuid: format!("bot-{}", uuid::Uuid::new_v4()),
            token: format!("test-token-{}", uuid::Uuid::new_v4()),
            bcs_url: server_url.to_string(),
            // api_base_url 留空，让 env var BCS_API_BASE_URL 生效
            api_base_url: None,
        }
    }

    /// Write session to a temp directory in the format expected by bcs-cli
    pub fn write_to_dir(&self, dir: &Path) -> anyhow::Result<PathBuf> {
        let bcs_dir = dir.join(".bcs");
        std::fs::create_dir_all(&bcs_dir)?;
        let session_path = bcs_dir.join("session.json");
        let content = serde_json::to_string_pretty(self)?;
        std::fs::write(&session_path, content)?;
        Ok(session_path)
    }
}

/// Test context containing mock server and temp files
#[allow(dead_code)]
pub struct TestContext {
    pub mock_server: MockServer,
    pub temp_dir: TempDir,
    pub session: MockSession,
}

#[allow(dead_code)]
impl TestContext {
    /// Create a new test context with running mock server
    pub async fn new() -> anyhow::Result<Self> {
        let mock_server = MockServer::start().await;
        let temp_dir = TempDir::new()?;
        let session = MockSession::new(&mock_server.uri());
        session.write_to_dir(temp_dir.path())?;

        Ok(Self {
            mock_server,
            temp_dir,
            session,
        })
    }

    /// Get the path to the session file
    pub fn session_path(&self) -> PathBuf {
        self.temp_dir.path().join(".bcs").join("session.json")
    }

    /// Get environment variables for CLI execution
    pub fn env(&self) -> HashMap<String, String> {
        let mut env = HashMap::new();
        env.insert(
            "BOT_DATA_DIR".to_string(),
            self.temp_dir.path().to_string_lossy().to_string(),
        );
        env.insert("BCS_API_BASE_URL".to_string(), self.mock_server.uri());
        env
    }

    /// Create a CLI command pre-configured with test environment
    pub fn cmd(&self) -> Command {
        let mut cmd = Command::cargo_bin("bcs-cli").expect("Failed to find bcs-cli binary");
        
        // Set required environment variables
        cmd.env("BOT_DATA_DIR", self.temp_dir.path());
        
        // Use --url arg instead of env var to ensure it takes priority
        cmd.arg("--url").arg(&self.mock_server.uri());
        
        cmd
    }

    /// Setup a mock for the health endpoint
    pub async fn mock_health(&self, healthy: bool) {
        let response = if healthy {
            ResponseTemplate::new(200).set_body_json(serde_json::json!({"status": "healthy"}))
        } else {
            ResponseTemplate::new(503)
                .set_body_json(serde_json::json!({"status": "unhealthy"}))
        };

        Mock::given(method("GET"))
            .and(path("/health"))
            .respond_with(response)
            .mount(&self.mock_server)
            .await;
    }

    /// Setup a mock for the list bots endpoint
    pub async fn mock_list_bots(&self, bots: Vec<serde_json::Value>) {
        // Server returns array directly: [{...}, {...}]
        let response = ResponseTemplate::new(200)
            .set_body_json(serde_json::Value::Array(bots));

        Mock::given(method("GET"))
            .and(path("/bots"))
            // query param "onboarded=true" will be present but wiremock matches path only
            .respond_with(response)
            .mount(&self.mock_server)
            .await;
    }

    /// Setup a mock for generic error responses
    pub async fn mock_error(&self, status: u16, message: &str) {
        let response = ResponseTemplate::new(status)
            .set_body_json(serde_json::json!({ "error": message }));

        Mock::given(method("GET"))
            .respond_with(response)
            .mount(&self.mock_server)
            .await;
    }
}

/// Assert that a CLI output contains expected text (case insensitive)
pub fn assert_output_contains(output: &Output, expected: &str) {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{} {}", stdout, stderr);
    
    assert!(
        combined.to_lowercase().contains(&expected.to_lowercase()),
        "Expected output to contain '{}' but got:\nstdout:\n{}\nstderr:\n{}",
        expected,
        stdout,
        stderr
    );
}

/// Assert successful CLI execution
pub fn assert_success(output: &Output) {
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        panic!(
            "Command failed with exit code {:?}\nstdout:\n{}\nstderr:\n{}",
            output.status.code(),
            stdout,
            stderr
        );
    }
}

/// Assert CLI failure with specific exit code
pub fn assert_failure(output: &Output, expected_code: Option<i32>) {
    if output.status.success() {
        panic!("Expected command to fail but it succeeded");
    }
    
    if let Some(expected) = expected_code {
        let actual = output.status.code();
        assert_eq!(
            actual,
            Some(expected),
            "Expected exit code {:?} but got {:?}",
            expected,
            actual
        );
    }
}
