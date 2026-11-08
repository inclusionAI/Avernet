#![cfg(unix)]

use std::{collections::HashMap, os::unix::fs::PermissionsExt, sync::{Arc, Mutex}};

use axum::{Router, extract::{Query, State}, http::StatusCode, routing::post};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use bcs_bridge_app::{BridgeApp, credentials::CredentialSource, registration::{RegisterArgs, register}};
use clap::Parser;
use serde_json::json;
use tokio::{net::TcpListener, task::JoinHandle};

#[derive(Parser)]
struct RegistrationCommand {
    #[command(flatten)]
    args: RegisterArgs,
}

struct TestCredentials;

impl CredentialSource for TestCredentials {
    fn webhook_token(&self, provider_id: &str) -> anyhow::Result<String> {
        assert_eq!(provider_id, "prv_test");
        Ok("test-webhook-secret".into())
    }
}

struct Api {
    base: String,
    calls: Arc<Mutex<Vec<HashMap<String, String>>>>,
    task: JoinHandle<()>,
}

impl Drop for Api {
    fn drop(&mut self) { self.task.abort(); }
}

impl Api {
    async fn new() -> Self {
        let calls = Arc::new(Mutex::new(Vec::new()));
        async fn handle(
            State(calls): State<Arc<Mutex<Vec<HashMap<String, String>>>>>,
            Query(query): Query<HashMap<String, String>>,
        ) -> (StatusCode, axum::Json<serde_json::Value>) {
            calls.lock().unwrap().push(query.clone());
            (StatusCode::CREATED, axum::Json(json!({
                "code": 20100,
                "data": {
                    "bot_uuid": format!("bot_{}", query["provider_bot_ref"]),
                    "bot_token": "test-bot-token",
                    "registration": {
                        "provider_id": "prv_test",
        "auth_mode": "static_bearer",
                        "provider_bot_ref": query["provider_bot_ref"],
                        "mode": query["mode"],
                    },
                },
            })))
        }
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let router = Router::new().route("/openapi/v1/collaboration/register", post(handle)).with_state(calls.clone());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        Self { base, calls, task }
    }
}

fn token() -> String {
    let mut bytes = serde_json::to_vec(&json!({
        "v": 2,
        "purpose": "provider_bot_registration",
        "id": "human_test",
        "provider_id": "prv_test",
        "auth_mode": "static_bearer",
        "allowed_modes": ["plugin", "gateway"],
        "exp": 4102444800u64,
    })).unwrap();
    bytes.extend_from_slice(&[0; 32]);
    URL_SAFE_NO_PAD.encode(bytes)
}

#[tokio::test]
async fn registration_permission_defaults_apply_only_to_the_selected_engine() {
    let api = Api::new().await;
    let app = BridgeApp::builder().credentials(TestCredentials)
        .registration_permission_mode("claude-code", "bypassPermissions").build();
    let token = token();
    for mode in ["gateway", "plugin"] {
        for engine in ["claude-code", "codex"] {
            let directory = tempfile::tempdir().unwrap();
            let engine_bin = directory.path().join("mock-engine");
            std::fs::write(&engine_bin, "#!/bin/sh\nexit 0\n").unwrap();
            std::fs::set_permissions(&engine_bin, std::fs::Permissions::from_mode(0o755)).unwrap();
            let provider_bot_ref = format!("{mode}-{engine}");
            let mut arguments = vec![
                "register", "--token", &token, "--bot-name", "Test Bot",
                "--provider-bot-ref", &provider_bot_ref,
                "--mode", mode, "--api-url", &api.base, "--engine", engine,
                "--cwd", directory.path().to_str().unwrap(), "--engine-bin", engine_bin.to_str().unwrap(),
            ];
            if mode == "gateway" {
                arguments.extend(["--webhook-url", "https://hook.example/webhook"]);
            } else {
                arguments.extend(["--upstream-url", "ws://127.0.0.1:21000/ws/bot"]);
            }
            let command = RegistrationCommand::try_parse_from(arguments).unwrap();
            let config_path = directory.path().join("bridge.toml");
            register(command.args, &config_path, &app).await.unwrap();
            let document: toml::Value = toml::from_str(&std::fs::read_to_string(&config_path).unwrap()).unwrap();
            let bot = &document["bot"][0];
            assert_eq!(document["mode"].as_str(), Some(mode));
            assert_eq!(bot["engine"].as_str(), Some(engine));
            assert_eq!(bot["provider_bot_ref"].as_str(), Some(provider_bot_ref.as_str()));
            assert_eq!(bot.get("permission_mode").and_then(toml::Value::as_str),
                if engine == "claude-code" { Some("bypassPermissions") } else { None });
        }
    }
    assert_eq!(api.calls.lock().unwrap().len(), 4);
}
