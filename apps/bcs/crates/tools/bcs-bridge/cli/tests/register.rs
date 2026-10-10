//! Registration through the open-source binary: static-bearer identity,
//! explicit endpoints and engine selection against a local registration API.

use std::{collections::HashMap, path::{Path, PathBuf}, process::Output, sync::{Arc, Mutex}};

use axum::{Router, extract::{Query, State}, http::StatusCode, response::IntoResponse, routing::post};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;
use tokio::{net::TcpListener, process::Command, task::JoinHandle};

const WEBHOOK_TOKEN: &str = "provider-webhook-secret";
const BOT_TOKEN: &str = "returned-bot-secret";
const PREFIX: &str = "/bcs";

type Calls = Arc<Mutex<Vec<HashMap<String, String>>>>;

/// Registration API that mirrors the requested binding, as BCS does on success.
struct Api { base: String, calls: Calls, task: JoinHandle<()> }
impl Drop for Api { fn drop(&mut self) { self.task.abort(); } }
impl Api {
    async fn new() -> Self {
        let calls: Calls = Arc::default();
        async fn register(State(calls): State<Calls>, Query(query): Query<HashMap<String, String>>) -> impl IntoResponse {
            calls.lock().unwrap().push(query.clone());
            let body = json!({"code":20100,"message":"Created","data":{"bot_uuid":"bot_new","bot_token":BOT_TOKEN,
                "registration":{"provider_id":"prv_test","provider_bot_ref":query["provider_bot_ref"],"mode":query["mode"]}}});
            (StatusCode::CREATED, axum::Json(body))
        }
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}{PREFIX}", listener.local_addr().unwrap());
        let app = Router::new().route(&format!("{PREFIX}/openapi/v1/collaboration/register"), post(register))
            .with_state(calls.clone());
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        Self { base, calls, task }
    }
    fn calls(&self) -> Vec<HashMap<String, String>> { self.calls.lock().unwrap().clone() }
}

/// v2 registration token with a synthetic signature; the API owns verification.
fn token() -> String {
    let mut bytes = serde_json::to_vec(&json!({"v":2,"purpose":"provider_bot_registration","id":"human_test",
        "provider_id":"prv_test","allowed_modes":["plugin","gateway"],"exp":4102444800u64})).unwrap();
    bytes.extend_from_slice(&[0; 32]);
    URL_SAFE_NO_PAD.encode(bytes)
}

fn install_engine(home: &Path, name: &str) {
    let path = home.join("bin").join(name);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(&path, "#!/bin/sh\nexit 0\n").unwrap();
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
}

fn register(home: &Path, args: &[&str]) -> Command {
    for name in ["claude", "codex"] { install_engine(home, name); }
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
    cmd.env("HOME", home).current_dir(home).env("PATH", home.join("bin"))
        .env_remove("BRIDGE_CONFIG").env_remove("BRIDGE_REGISTER_TOKEN")
        .env("BCS_BRIDGE_PROVIDER_TOKENS", json!({"prv_test": WEBHOOK_TOKEN}).to_string())
        .env("NO_PROXY", "127.0.0.1,localhost").env("no_proxy", "127.0.0.1,localhost");
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"] { cmd.env_remove(key); }
    cmd.args(["register", "--token", &token(), "--bot-name", "Test Bot"]).args(args);
    cmd
}

fn config_path(home: &Path) -> PathBuf { home.join(".bcn-bridge/bridge.toml") }

fn saved(home: &Path) -> toml::Value {
    toml::from_str(&std::fs::read_to_string(config_path(home)).unwrap()).unwrap()
}

fn diagnostic(output: &Output) -> String {
    format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr))
}

#[tokio::test]
async fn gateway_registration_uses_the_explicit_provider_bot_ref_and_default_engine() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let output = register(home.path(), &["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook",
        "--provider-bot-ref", "worker-1"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert!(!diagnostic(&output).contains(BOT_TOKEN));
    let calls = api.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0]["provider_bot_ref"], "worker-1");
    assert_eq!(calls[0]["mode"], "gateway");
    let document = saved(home.path());
    let bot = &document["bot"][0];
    assert_eq!(bot["provider_bot_ref"].as_str(), Some("worker-1"));
    assert_eq!(bot["engine"].as_str(), Some("claude-code"));
    assert_eq!(bot["engine_bin"].as_str(), home.path().join("bin/claude").canonicalize().unwrap().to_str());
    assert!(bot.get("model").is_none(), "no default model is configured");
    assert_eq!(document["registration"]["api_url"].as_str(), Some(api.base.as_str()));
}

#[tokio::test]
async fn plugin_registration_generates_a_provider_bot_ref_when_none_is_given() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let output = register(home.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "codex", "--model", "gpt-5"])
        .output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let generated = api.calls()[0]["provider_bot_ref"].clone();
    assert!(generated.starts_with("bridge-") && generated.len() > "bridge-".len(), "{generated}");
    let document = saved(home.path());
    let bot = &document["bot"][0];
    assert_eq!(bot["provider_bot_ref"].as_str(), Some(generated.as_str()));
    assert_eq!(bot["engine"].as_str(), Some("codex"));
    assert_eq!(bot["model"].as_str(), Some("gpt-5"));
    assert_eq!(document["plugin"]["url"].as_str(), Some("ws://127.0.0.1:21000/ws/bot"));
}

#[tokio::test]
async fn missing_explicit_inputs_are_rejected_before_registration() {
    let api = Api::new().await;
    for (args, expected) in [
        (vec!["--mode", "gateway", "--webhook-url", "https://hook.example/webhook", "--provider-bot-ref", "worker-1"], "--api-url"),
        (vec!["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook"], "--provider-bot-ref"),
        (vec!["--api-url", &api.base], "--upstream-url"),
        (vec!["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook", "--provider-bot-ref", "bad ref"], "--provider-bot-ref"),
        (vec!["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook", "--engine", "bogus"], "claude-code, codex"),
        (vec!["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook", "--provider-auth", "unknown-auth"], "static-bearer"),
    ] {
        let home = tempfile::tempdir().unwrap();
        let output = register(home.path(), &args).output().await.unwrap();
        assert!(!output.status.success(), "accepted {args:?}");
        assert!(diagnostic(&output).contains(expected), "{args:?}: {}", diagnostic(&output));
        assert!(!config_path(home.path()).exists());
    }
    assert!(api.calls().is_empty());
}

#[tokio::test]
async fn help_lists_the_open_source_defaults() {
    let home = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).env("HOME", home.path())
        .args(["register", "--help"]).output().await.unwrap();
    assert!(output.status.success());
    let help = diagnostic(&output);
    for expected in ["--provider-auth", "static-bearer", "--engine", "claude-code", "--engine-bin", "--api-url"] {
        assert!(help.contains(expected), "missing {expected}: {help}");
    }
}
