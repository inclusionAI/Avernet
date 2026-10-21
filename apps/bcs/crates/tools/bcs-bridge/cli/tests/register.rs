//! Registration through the open-source binary: static-bearer identity,
//! explicit endpoints and engine selection against a local registration API.

use std::{collections::HashMap, path::{Path, PathBuf}, process::Output, sync::{Arc, Mutex}};

use axum::{Router, extract::{Query, State}, http::{StatusCode, HeaderMap}, response::IntoResponse, routing::post};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;
use tokio::{net::TcpListener, process::Command, task::JoinHandle};

#[path = "register/subbots.rs"]
mod subbots;

const WEBHOOK_TOKEN: &str = "provider-webhook-secret";
const BOT_TOKEN: &str = "returned-bot-secret";
const PREFIX: &str = "/bcs";

type Calls = Arc<Mutex<Vec<HashMap<String, String>>>>;

/// Registration API that mirrors the requested binding, as BCS does on success.
struct Api { base: String, calls: Calls, patches: Calls, task: JoinHandle<()> }
impl Drop for Api { fn drop(&mut self) { self.task.abort(); } }
impl Api {
    async fn new() -> Self {
        let calls: Calls = Arc::default();
        let patches: Calls = Arc::default();
        async fn register(State(calls): State<Calls>, Query(query): Query<HashMap<String, String>>) -> impl IntoResponse {
            let mut calls = calls.lock().unwrap();
            let duplicate = calls.iter().any(|old| old["provider_bot_ref"] == query["provider_bot_ref"]);
            calls.push(query.clone());
            if duplicate { return (StatusCode::CONFLICT, axum::Json(json!({"error":"duplicate binding"}))); }
            let bot_uuid = if calls.len() == 1 { "bot_new".to_owned() } else { format!("bot_new_{}", calls.len()) };
            let body = json!({"code":20100,"message":"Created","data":{"bot_uuid":bot_uuid,"bot_token":BOT_TOKEN,
                "registration":{"provider_id":"prv_test","auth_mode":"static_bearer","provider_bot_ref":query["provider_bot_ref"],"mode":query["mode"]}}});
            (StatusCode::CREATED, axum::Json(body))
        }
        async fn patch_webhook(State(patches): State<Calls>, headers: HeaderMap, axum::Json(body): axum::Json<serde_json::Value>) -> impl IntoResponse {
            let webhook = body["webhook_url"].as_str().unwrap();
            let auth = headers.get("authorization").and_then(|v| v.to_str().ok()).unwrap_or("");
            patches.lock().unwrap().push(HashMap::from([("webhook".into(), webhook.into()), ("authorization".into(), auth.into())]));
            if auth != "Bearer test-admin" || webhook.ends_with("/fail") {
                return (StatusCode::FORBIDDEN, axum::Json(json!({"error":"forbidden"})));
            }
            (StatusCode::OK, axum::Json(json!({"bot_uuid":"bot_new","provider_id":"prv_test","auth_mode":"static_bearer","provider_bot_ref":"worker-a","webhook_url":webhook})))
        }
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}{PREFIX}", listener.local_addr().unwrap());
        let app = Router::new().route(&format!("{PREFIX}/openapi/v1/collaboration/register"), post(register))
            .with_state(calls.clone());
        let app = app.merge(Router::new().route(&format!("{PREFIX}/providers/prv_test/bots/worker-a"), axum::routing::patch(patch_webhook)).with_state(patches.clone()));
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        Self { base, calls, patches, task }
    }
    fn calls(&self) -> Vec<HashMap<String, String>> { self.calls.lock().unwrap().clone() }
}

/// v2 registration token with a synthetic signature; the API owns verification.
fn token_with_auth(auth_mode: &str) -> String {
    let mut bytes = serde_json::to_vec(&json!({"v":2,"purpose":"provider_bot_registration","id":"human_test",
        "provider_id":"prv_test","auth_mode":auth_mode,"allowed_modes":["plugin","gateway"],"exp":4102444800u64})).unwrap();
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

fn register(home: &Path, args: &[&str]) -> Command { register_as(home, args, "static_bearer") }

fn register_as(home: &Path, args: &[&str], auth_mode: &str) -> Command {
    register_with_token(home, args, &token_with_auth(auth_mode))
}

fn register_with_token(home: &Path, args: &[&str], token: &str) -> Command {
    for name in ["claude", "codex"] { install_engine(home, name); }
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
    cmd.env("HOME", home).current_dir(home).env("PATH", home.join("bin"))
        .env_remove("BRIDGE_CONFIG").env_remove("BRIDGE_REGISTER_TOKEN").env_remove("BRIDGE_PROVIDER_ADMIN_TOKEN")
        .env("BCS_BRIDGE_PROVIDER_TOKENS", json!({"prv_test": WEBHOOK_TOKEN}).to_string())
        .env("NO_PROXY", "127.0.0.1,localhost").env("no_proxy", "127.0.0.1,localhost");
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"] { cmd.env_remove(key); }
    cmd.args(["register", "--token", token, "--bot-name", "Test Bot"]).args(args);
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
    assert_eq!(Path::new(bot["engine_bin"].as_str().unwrap()).canonicalize().unwrap(),
        home.path().join("bin/claude").canonicalize().unwrap());
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

#[cfg(unix)]
#[tokio::test]
async fn desktop_registration_persists_socket_and_rejects_invalid_options_before_network() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let directory = home.path().join("app-server-control");
    std::fs::create_dir(&directory).unwrap();
    let socket = directory.join("app-server-control.sock");
    let listener = tokio::net::UnixListener::bind(&socket).unwrap();
    let peer = tokio::spawn(async move {
        use futures::{SinkExt, StreamExt};
        for _ in 0..2 {
            let (stream, _) = listener.accept().await.unwrap();
            let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
            let request: serde_json::Value = serde_json::from_str(ws.next().await.unwrap().unwrap().to_text().unwrap()).unwrap();
            assert_eq!(request["method"], "initialize");
            ws.send(json!({"id":request["id"],"result":{"userAgent":"codex/test"}}).to_string().into()).await.unwrap();
            while let Some(frame) = ws.next().await {
                let frame = frame.unwrap();
                if frame.is_close() { break; }
                let value: serde_json::Value = serde_json::from_str(frame.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], "initialized");
            }
        }
    });
    let option = format!("socket={}", socket.display());
    let output = register(home.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "codex-desktop",
        "--engine-option", &option])
        .output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let document = saved(home.path());
    assert_eq!(document["bot"][0]["engine"].as_str(), Some("codex-desktop"));
    assert_eq!(document["bot"][0]["engine_options"]["socket"].as_str(), socket.to_str());
    for option in ["socket=relative.sock", "socket=/tmp/a", "unknown=value", "missing-separator"] {
        let other = tempfile::tempdir().unwrap();
        let args = if option == "socket=/tmp/a" {
            vec!["--engine-option", option, "--engine-option", option]
        } else { vec!["--engine-option", option] };
        let output = register(other.path(), &["--mode", "plugin", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "codex-desktop"])
            .args(args).output().await.unwrap();
        assert!(!output.status.success(), "{option}");
        assert!(!config_path(other.path()).exists());
    }
    let automatic = tempfile::tempdir().unwrap();
    let output = register(automatic.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "codex-desktop"])
        .env("CODEX_HOME", home.path()).env("PATH", "").output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert!(saved(automatic.path())["bot"][0].get("engine_options").is_none());
    peer.await.unwrap();
    let absent = tempfile::tempdir().unwrap();
    let output = register(absent.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "codex-desktop"])
        .env("CODEX_HOME", absent.path()).output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("desktop detection failed"));
    assert!(!config_path(absent.path()).exists());
    assert_eq!(api.calls().len(), 2);
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
        (vec!["--mode", "gateway", "--api-url", &api.base, "--webhook-url", "https://hook.example/webhook", "--provider-auth", "unknown-auth"], "unavailable in this bridge"),
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
    for expected in ["--engine", "claude-code", "--engine-bin", "--api-url"] {
        assert!(help.contains(expected), "missing {expected}: {help}");
    }
}

#[tokio::test]
async fn desktop_engine_registration_needs_no_executable_and_rejects_engine_bin() {
    let api = Api::new().await;
    let home = tempfile::tempdir().unwrap();
    let output = register(home.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "qwenwork"])
        .env("PATH", "").output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(saved(home.path())["bot"][0]["engine"].as_str(), Some("qwenwork"));
    assert!(saved(home.path())["bot"][0].get("engine_bin").is_none());
    let other = tempfile::tempdir().unwrap();
    let output = register(other.path(), &["--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--engine", "qwenwork", "--engine-bin", "/missing"])
        .output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("--engine-bin does not apply"));
    assert_eq!(api.calls().len(), 1);
}


#[tokio::test]
async fn named_profiles_keep_separate_configs_and_reuse_existing_registration() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    for profile in ["local", "pre", "prod"] {
        let args = ["--profile", profile, "--mode", "plugin", "--engine", "qwenwork",
            "--api-url", &api.base, "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        let output = register(home.path(), &args).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let path = home.path().join(format!(".bcn-bridge/profiles/{profile}/bridge.toml"));
        let original = std::fs::read(&path).unwrap();
        let calls = api.calls().len();
        let repeated = register(home.path(), &args).output().await.unwrap();
        assert!(repeated.status.success(), "{}", diagnostic(&repeated));
        assert!(diagnostic(&repeated).contains("already registered"));
        assert_eq!(api.calls().len(), calls);
        assert_eq!(std::fs::read(path).unwrap(), original);
    }
    assert!(!config_path(home.path()).exists());
}

#[tokio::test]
async fn named_profile_refuses_changed_endpoint_without_registration_or_overwrite() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "plugin", "--engine", "qwenwork", "--api-url", &api.base];
    let output = register(home.path(), &args).args(["--upstream-url", "ws://127.0.0.1:21000/ws/bot"])
        .output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let original = std::fs::read(&path).unwrap();
    let output = register(home.path(), &args).args(["--upstream-url", "ws://127.0.0.1:22000/ws/bot"])
        .output().await.unwrap();
    assert!(!output.status.success());
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}

#[tokio::test]
async fn replacement_retains_a_private_backup_identity_and_state() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--mode", "plugin", "--engine", "qwenwork", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    let output = register(home.path(), &args).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let original = std::fs::read(config_path(home.path())).unwrap();
    let original_state = saved(home.path())["state_path"].as_str().unwrap().to_owned();
    std::fs::write(config_path(home.path()).parent().unwrap().join(&original_state), "existing state").unwrap();
    let output = register(home.path(), &args).arg("--replace-existing").output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(saved(home.path())["state_path"].as_str().unwrap(), original_state);
    assert_eq!(api.calls().len(), 1);
    assert_eq!(saved(home.path())["bot"][0]["token"].as_str(), Some(BOT_TOKEN));
    let parent = config_path(home.path()).parent().unwrap().to_owned();
    assert_eq!(std::fs::read_to_string(parent.join(&original_state)).unwrap(), "existing state");
    let backup = std::fs::read_dir(parent).unwrap().map(|entry| entry.unwrap().path())
        .find(|path| path.file_name().unwrap().to_string_lossy().starts_with(".bridge-backup-")).unwrap();
    assert_eq!(std::fs::read(&backup).unwrap(), original);
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(backup).unwrap().permissions().mode() & 0o777, 0o600);
    }
}


#[tokio::test]
async fn profile_reuse_rejects_a_changed_explicit_provider_bot_reference() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "plugin", "--engine", "qwenwork", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    let output = register(home.path(), &args).args(["--provider-bot-ref", "worker-a"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let original = std::fs::read(&path).unwrap();
    let same = register(home.path(), &args).args(["--provider-bot-ref", "worker-a"]).output().await.unwrap();
    assert!(same.status.success(), "{}", diagnostic(&same));
    let changed = register(home.path(), &args).args(["--provider-bot-ref", "worker-b"]).output().await.unwrap();
    assert!(!changed.status.success());
    assert!(diagnostic(&changed).contains("different provider Bot reference"));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}

#[tokio::test]
async fn gateway_profile_reuse_verifies_the_saved_webhook_endpoint() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "gateway", "--engine", "qwenwork", "--api-url", &api.base,
        "--provider-bot-ref", "worker-a"];
    let output = register(home.path(), &args).args(["--webhook-url", "https://hook.example/webhook"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let original = std::fs::read(&path).unwrap();
    let document: toml::Value = toml::from_str(std::str::from_utf8(&original).unwrap()).unwrap();
    assert_eq!(document["registration"]["webhook_url"].as_str(), Some("https://hook.example/webhook"));
    let repeated = register(home.path(), &args).args(["--webhook-url", "https://hook.example/webhook"]).output().await.unwrap();
    assert!(repeated.status.success(), "{}", diagnostic(&repeated));
    let omitted = register(home.path(), &args).output().await.unwrap();
    assert!(omitted.status.success(), "{}", diagnostic(&omitted));
    for webhook in ["https://hook.example/new", "https://hook.example/webhook/", "https://hook.example/webhook?key=changed"] {
        let changed = register(home.path(), &args).args(["--webhook-url", webhook]).output().await.unwrap();
        assert!(!changed.status.success());
        assert!(diagnostic(&changed).contains("different BCS endpoint or gateway webhook"));
    }
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}

#[tokio::test]
async fn legacy_gateway_profile_without_webhook_metadata_requires_replacement() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "gateway", "--engine", "qwenwork", "--api-url", &api.base,
        "--provider-bot-ref", "worker-a", "--webhook-url", "https://hook.example/webhook"];
    let output = register(home.path(), &args).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let mut document: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    document["registration"].as_table_mut().unwrap().remove("webhook_url");
    std::fs::write(&path, toml::to_string(&document).unwrap()).unwrap();
    let original = std::fs::read(&path).unwrap();
    let repeated = register(home.path(), &args).output().await.unwrap();
    assert!(!repeated.status.success());
    assert!(diagnostic(&repeated).contains("Cannot verify this profile's gateway webhook"));
    assert!(diagnostic(&repeated).contains("--replace-existing"));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}


#[tokio::test]
async fn profile_reuse_checks_explicit_executable_paths_without_resolving_symlink_identity() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    install_engine(home.path(), "codex-next");
    let old_bin = home.path().join("bin/codex");
    let next_bin = home.path().join("bin/codex-next");
    let args = ["--profile", "pre", "--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    let output = register(home.path(), &args).args(["--engine-bin", old_bin.to_str().unwrap()]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    // Legacy configs may retain a directory alias instead of the normalized parent.
    let mut legacy: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    legacy["bot"][0]["engine_bin"] = old_bin.to_str().unwrap().into();
    std::fs::write(&path, toml::to_string(&legacy).unwrap()).unwrap();
    let original = std::fs::read(&path).unwrap();
    let repeated = register(home.path(), &args).args(["--engine-bin", "bin/codex"]).output().await.unwrap();
    assert!(repeated.status.success(), "{}", diagnostic(&repeated));
    let omitted = register(home.path(), &args).output().await.unwrap();
    assert!(omitted.status.success(), "{}", diagnostic(&omitted));
    let changed = register(home.path(), &args).args(["--engine-bin", next_bin.to_str().unwrap()]).output().await.unwrap();
    assert!(!changed.status.success());
    assert!(diagnostic(&changed).contains("different engine executable"));
    #[cfg(unix)] {
        let alias = home.path().join("bin/codex-alias");
        std::os::unix::fs::symlink(&old_bin, &alias).unwrap();
        let changed = register(home.path(), &args).args(["--engine-bin", alias.to_str().unwrap()]).output().await.unwrap();
        assert!(!changed.status.success());
        assert!(diagnostic(&changed).contains("different engine executable"));
    }
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}

#[tokio::test]
async fn gateway_profile_reuse_checks_only_explicit_listener_changes_including_default_value() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "gateway", "--engine", "qwenwork", "--api-url", &api.base,
        "--provider-bot-ref", "worker-a", "--webhook-url", "https://hook.example/webhook"];
    let output = register(home.path(), &args).args(["--listen", "127.0.0.1:18001"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let original = std::fs::read(&path).unwrap();
    let same = register(home.path(), &args).args(["--listen", "127.0.0.1:18001"]).output().await.unwrap();
    assert!(same.status.success(), "{}", diagnostic(&same));
    let omitted = register(home.path(), &args).output().await.unwrap();
    assert!(omitted.status.success(), "{}", diagnostic(&omitted));
    for address in ["127.0.0.1:18002", "0.0.0.0:8322"] {
        let changed = register(home.path(), &args).args(["--listen", address]).output().await.unwrap();
        assert!(!changed.status.success());
        assert!(diagnostic(&changed).contains("different gateway listener"));
    }
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(path).unwrap(), original);
}

#[tokio::test]
async fn replacement_updates_local_settings_without_duplicate_registration() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "plugin", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--provider-bot-ref", "worker-a"];
    assert!(register(home.path(), &args).output().await.unwrap().status.success());
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let before: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    install_engine(home.path(), "claude-next");
    let output = register(home.path(), &args).args(["--replace-existing", "--model", "changed-model", "--engine-bin", "bin/claude-next"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let after: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    for key in ["bot_id", "token", "provider_bot_ref"] { assert_eq!(after["bot"][0][key], before["bot"][0][key]); }
    assert_eq!(after["state_path"], before["state_path"]);
    assert_eq!(after["bot"][0]["model"].as_str(), Some("changed-model"));
    assert!(after["bot"][0]["engine_bin"].as_str().unwrap().ends_with("bin/claude-next"));
    let repeated = register(home.path(), &args).arg("--replace-existing").output().await.unwrap();
    assert!(repeated.status.success(), "{}", diagnostic(&repeated));
    let retained: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(retained["bot"][0]["engine_bin"], after["bot"][0]["engine_bin"]);
    assert_eq!(retained["bot"][0]["model"], after["bot"][0]["model"]);
    assert_eq!(api.calls().len(), 1);
}

#[tokio::test]
async fn gateway_replacement_patches_webhook_with_admin_auth_and_preserves_identity() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "gateway", "--engine", "qwenwork", "--api-url", &api.base,
        "--provider-bot-ref", "worker-a"];
    assert!(register(home.path(), &args).args(["--webhook-url", "https://hook.example/old"]).output().await.unwrap().status.success());
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let before = std::fs::read(&path).unwrap();
    let missing = register(home.path(), &args).args(["--replace-existing", "--webhook-url", "https://hook.example/new"]).output().await.unwrap();
    assert!(!missing.status.success());
    assert!(diagnostic(&missing).contains("--provider-admin-token"));
    assert_eq!(std::fs::read(&path).unwrap(), before);
    assert!(api.patches.lock().unwrap().is_empty());
    let rejected = register(home.path(), &args).args(["--replace-existing", "--webhook-url", "https://hook.example/fail"])
        .env("BRIDGE_PROVIDER_ADMIN_TOKEN", "test-admin").output().await.unwrap();
    assert!(!rejected.status.success());
    assert_eq!(std::fs::read(&path).unwrap(), before);
    let updated = register(home.path(), &args).args(["--replace-existing", "--webhook-url", "https://hook.example/new", "--listen", "127.0.0.1:18001"])
        .env("BRIDGE_PROVIDER_ADMIN_TOKEN", "test-admin").output().await.unwrap();
    assert!(updated.status.success(), "{}", diagnostic(&updated));
    let after: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(after["registration"]["webhook_url"].as_str(), Some("https://hook.example/new"));
    assert_eq!(after["listen"].as_str(), Some("127.0.0.1:18001"));
    assert_eq!(after["bot"][0]["bot_id"].as_str(), Some("bot_new"));
    assert_eq!(after["bot"][0]["token"].as_str(), Some(BOT_TOKEN));
    assert!(!std::fs::read_to_string(&path).unwrap().contains("test-admin"));
    assert!(!diagnostic(&updated).contains("test-admin"));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(api.patches.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn replacement_with_new_reference_creates_new_identity_and_fresh_state() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--mode", "plugin", "--engine", "qwenwork", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    assert!(register(home.path(), &args).args(["--provider-bot-ref", "worker-a"]).output().await.unwrap().status.success());
    let before = saved(home.path());
    let output = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", "worker-b"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let after = saved(home.path());
    assert_eq!(after["bot"][0]["provider_bot_ref"].as_str(), Some("worker-b"));
    assert_ne!(after["state_path"], before["state_path"]);
    assert_eq!(api.calls().len(), 2);
}

#[tokio::test]
async fn gateway_local_replacement_retains_omitted_webhook_without_admin_or_registration() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--profile", "pre", "--mode", "gateway", "--engine", "qwenwork", "--api-url", &api.base,
        "--provider-bot-ref", "worker-a"];
    assert!(register(home.path(), &args).args(["--webhook-url", "https://hook.example/old"]).output().await.unwrap().status.success());
    let path = home.path().join(".bcn-bridge/profiles/pre/bridge.toml");
    let output = register(home.path(), &args).args(["--replace-existing", "--listen", "127.0.0.1:18003"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let after: toml::Value = toml::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(after["listen"].as_str(), Some("127.0.0.1:18003"));
    assert_eq!(after["registration"]["webhook_url"].as_str(), Some("https://hook.example/old"));
    assert_eq!(api.calls().len(), 1);
    assert!(api.patches.lock().unwrap().is_empty());
    let bytes = std::fs::read(&path).unwrap();
    let changed = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", "worker-b"]).output().await.unwrap();
    assert!(!changed.status.success());
    assert_eq!(std::fs::read(&path).unwrap(), bytes);
    assert_eq!(api.calls().len(), 1);
}

#[tokio::test]
async fn stopped_qwen_profiles_reuse_shared_desktop_settings_and_reject_ambiguous_legacy_settings() {
    let home = tempfile::tempdir().unwrap();
    let old_api = Api::new().await;
    let args = ["--mode", "plugin", "--engine", "qwenwork", "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    assert!(register(home.path(), &args).args(["--profile", "local", "--api-url", &old_api.base]).output().await.unwrap().status.success());
    let db_path = home.path().join("agents.db");
    let settings = home.path().join("settings.json");
    let db = rusqlite::Connection::open(&db_path).unwrap();
    db.execute_batch("CREATE TABLE chats(id TEXT, ext TEXT, deleted_at INTEGER); CREATE TABLE sub_chats(session_id TEXT, chat_id TEXT);").unwrap();
    let initialized = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).env("HOME", home.path())
        .args(["--profile", "local", "init", "--engine", "qwenwork", "--option"])
        .arg(format!("database_path={}", db_path.display())).arg("--option")
        .arg(format!("settings_path={}", settings.display())).output().await.unwrap();
    assert!(initialized.status.success(), "{}", diagnostic(&initialized));
    let directory = home.path().join(".bcn-bridge");
    let old_path = directory.join("profiles/local/bridge.toml");
    let original = std::fs::read_to_string(&old_path).unwrap();
    let old: toml::Value = toml::from_str(&original).unwrap();
    let hooks = std::fs::read(&settings).unwrap();
    // No runtime record or process exists; only saved configuration remains.
    assert!(!directory.join("bridge.pid.json").exists());
    let api = Api::new().await;
    let registered = register(home.path(), &args).args(["--profile", "pre", "--api-url", &api.base]).output().await.unwrap();
    assert!(registered.status.success(), "{}", diagnostic(&registered));
    let pre_path = directory.join("profiles/pre/bridge.toml");
    let pre: toml::Value = toml::from_str(&std::fs::read_to_string(&pre_path).unwrap()).unwrap();
    assert_eq!(pre["bot"][0]["engine_options"], old["bot"][0]["engine_options"]);
    assert_ne!(pre["bot"][0]["provider_bot_ref"], old["bot"][0]["provider_bot_ref"]);
    let initialized = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).env("HOME", home.path())
        .args(["--profile", "pre", "init", "--engine", "qwenwork"]).output().await.unwrap();
    assert!(initialized.status.success(), "{}", diagnostic(&initialized));
    assert_eq!(std::fs::read(&settings).unwrap(), hooks);
    assert_eq!(std::fs::read_to_string(&old_path).unwrap(), original);
    // The legacy --config location is also discoverable; differing credentials must not be guessed.
    std::fs::write(directory.join("qwenwork.toml"), &original).unwrap();
    let prod_api = Api::new().await;
    assert!(register(home.path(), &args).args(["--profile", "prod", "--api-url", &prod_api.base]).output().await.unwrap().status.success());
    let mut conflict = old.clone();
    conflict["bot"][0]["engine_options"]["hook_secret"] = "different-hook-secret".into();
    std::fs::write(directory.join("qwenwork.toml"), toml::to_string(&conflict).unwrap()).unwrap();
    let migrated = register(home.path(), &args).args(["--profile", "other", "--api-url", &prod_api.base]).output().await.unwrap();
    assert!(migrated.status.success(), "{}", diagnostic(&migrated));
    let other: toml::Value = toml::from_str(&std::fs::read_to_string(directory.join("profiles/other/bridge.toml")).unwrap()).unwrap();
    assert_eq!(other["bot"][0]["engine_options"], old["bot"][0]["engine_options"]);
    std::fs::remove_file(settings.with_file_name("bcs-bridge.toml")).unwrap();
    let rejected = register(home.path(), &args).args(["--profile", "ambiguous", "--api-url", &prod_api.base]).output().await.unwrap();
    assert!(!rejected.status.success());
    assert!(diagnostic(&rejected).contains("conflicting desktop settings"));
    assert_eq!(prod_api.calls().len(), 2);
    assert!(!directory.join("profiles/ambiguous/bridge.toml").exists());
    assert_eq!(std::fs::read(&settings).unwrap(), hooks);
}


async fn assert_codex_replacement_updates_options(new_identity: bool) {
    for has_saved_options in [true, false] {
        let home = tempfile::tempdir().unwrap();
        let api = Api::new().await;
        let args = ["--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        let mut initial = register(home.path(), &args);
        initial.args(["--provider-bot-ref", "worker-a"]);
        if has_saved_options {
            initial.args(["--engine-option", "sandbox=danger-full-access", "--engine-option", "reasoning_effort=high"]);
        }
        let output = initial.output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let mut before = saved(home.path());
        if has_saved_options {
            // Keep a typed, non-default policy that the partial CLI update must not reset.
            before["bot"][0]["engine_options"].as_table_mut().unwrap().insert("network_access".into(), false.into());
            std::fs::write(config_path(home.path()), toml::to_string(&before).unwrap()).unwrap();
        }
        let reference = if new_identity { "worker-b" } else { "worker-a" };
        let output = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", reference,
            "--engine-option", "sandbox=read-only"]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let after = saved(home.path());
        let options = &after["bot"][0]["engine_options"];
        assert_eq!(options["sandbox"].as_str(), Some("read-only"));
        if has_saved_options {
            assert_eq!(options["network_access"].as_bool(), Some(false));
            assert_eq!(options["reasoning_effort"].as_str(), Some("high"));
        }
        if new_identity {
            assert_ne!(after["bot"][0]["bot_id"], before["bot"][0]["bot_id"]);
            assert_ne!(after["state_path"], before["state_path"]);
            assert_eq!(api.calls().len(), 2);
        } else {
            for key in ["bot_id", "token", "provider_bot_ref"] { assert_eq!(after["bot"][0][key], before["bot"][0][key]); }
            assert_eq!(after["state_path"], before["state_path"]);
            assert_eq!(api.calls().len(), 1);
            assert!(diagnostic(&output).contains("Bridge settings updated"));
        }
    }
}

#[tokio::test]
async fn codex_replacement_applies_explicit_options_to_existing_identity() {
    assert_codex_replacement_updates_options(false).await;
}

#[tokio::test]
async fn codex_replacement_applies_explicit_options_to_new_identity() {
    assert_codex_replacement_updates_options(true).await;
}

#[tokio::test]
async fn codex_replacement_preserves_options_when_none_are_supplied() {
    for new_identity in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let api = Api::new().await;
        let args = ["--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        let output = register(home.path(), &args).args(["--provider-bot-ref", "worker-a", "--engine-option",
            "sandbox=read-only", "--engine-option", "reasoning_effort=high"]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let before = saved(home.path());
        let reference = if new_identity { "worker-b" } else { "worker-a" };
        let output = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", reference]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        assert_eq!(saved(home.path())["bot"][0]["engine_options"], before["bot"][0]["engine_options"]);
        assert_eq!(api.calls().len(), if new_identity { 2 } else { 1 });
    }
}

#[tokio::test]
async fn codex_replacement_validates_explicit_options_with_retained_options() {
    for new_identity in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let api = Api::new().await;
        let args = ["--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        let output = register(home.path(), &args).args(["--provider-bot-ref", "worker-a", "--engine-option",
            "approval_policy=on-request", "--engine-option", "approval_review=auto"]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let reference = if new_identity { "worker-b" } else { "worker-a" };
        // This partial override is valid only together with the saved auto reviewer.
        let output = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", reference,
            "--engine-option", "approval_policy=untrusted"]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let after = saved(home.path());
        assert_eq!(after["bot"][0]["engine_options"]["approval_policy"].as_str(), Some("untrusted"));
        assert_eq!(after["bot"][0]["engine_options"]["approval_review"].as_str(), Some("auto"));
        assert_eq!(api.calls().len(), if new_identity { 2 } else { 1 });
    }
}

#[tokio::test]
async fn codex_replacement_rejects_invalid_merged_options_before_saving_or_registration() {
    for new_identity in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let api = Api::new().await;
        let args = ["--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        let output = register(home.path(), &args).args(["--provider-bot-ref", "worker-a", "--engine-option",
            "approval_policy=on-request", "--engine-option", "approval_review=auto"]).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        let original = std::fs::read(config_path(home.path())).unwrap();
        let reference = if new_identity { "worker-b" } else { "worker-a" };
        // Valid against defaults, but invalid with the saved on-request policy.
        let output = register(home.path(), &args).args(["--replace-existing", "--provider-bot-ref", reference,
            "--engine-option", "approval_review=user"]).output().await.unwrap();
        assert!(!output.status.success(), "{}", diagnostic(&output));
        assert!(diagnostic(&output).contains("Invalid engine options"), "{}", diagnostic(&output));
        assert_eq!(std::fs::read(config_path(home.path())).unwrap(), original);
        assert_eq!(api.calls().len(), 1);
    }
}

#[tokio::test]
async fn codex_options_are_not_inherited_when_registering_a_different_engine() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--mode", "plugin", "--api-url", &api.base, "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    let output = register(home.path(), &args).args(["--engine", "codex", "--provider-bot-ref", "worker-a",
        "--engine-option", "sandbox=danger-full-access"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let output = register(home.path(), &args).args(["--replace-existing", "--engine", "qwenwork",
        "--provider-bot-ref", "worker-b"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let after = saved(home.path());
    assert_eq!(after["bot"][0]["engine"].as_str(), Some("qwenwork"));
    assert!(after["bot"][0].get("engine_options").is_none());
    assert_eq!(api.calls().len(), 2);
}

#[cfg(unix)]
#[tokio::test]
async fn new_profile_reuses_only_desktop_options_from_the_verified_active_instance() {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    let home = tempfile::tempdir().unwrap();
    let old_api = Api::new().await;
    let args = ["--mode", "plugin", "--engine", "codex", "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
    let initial = register(home.path(), &args).args(["--api-url", &old_api.base, "--provider-bot-ref", "worker-a",
        "--engine-option", "sandbox=read-only"]).output().await.unwrap();
    assert!(initial.status.success(), "{:?}: {}", initial.status, diagnostic(&initial));
    let mut old = saved(home.path());
    old["bot"][0]["token"] = "old-bot-token".into();
    let original = toml::to_string(&old).unwrap();
    std::fs::write(config_path(home.path()), &original).unwrap();
    let directory = home.path().join(".bcn-bridge");
    let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
        .open(directory.join("bridge.lock")).unwrap();
    lock.try_lock().unwrap();
    let record = json!({"pid": 12345, "instance_id": "test-instance", "config": config_path(home.path()), "started_at": 0});
    std::fs::write(directory.join("bridge.pid.json"), serde_json::to_vec(&record).unwrap()).unwrap();
    let socket = tokio::net::UnixListener::bind(directory.join("bridge.sock")).unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = socket.accept().await.unwrap();
        let mut reader = BufReader::new(stream);
        let mut request = String::new();
        reader.read_line(&mut request).await.unwrap();
        let request: serde_json::Value = serde_json::from_str(&request).unwrap();
        assert_eq!(request["command"], "status");
        assert_eq!(request["instance_id"], record["instance_id"]);
        let response = json!({"record": record, "state": "running"}).to_string() + "\n";
        reader.get_mut().write_all(response.as_bytes()).await.unwrap();
    });
    let api = Api::new().await;
    let output = register(home.path(), &args).args(["--profile", "pre", "--api-url", &api.base,
        "--provider-bot-ref", "worker-b"]).output().await.unwrap();
    assert!(output.status.success(), "{:?}: {}", output.status, diagnostic(&output));
    task.await.unwrap();
    let new: toml::Value = toml::from_str(&std::fs::read_to_string(directory.join("profiles/pre/bridge.toml")).unwrap()).unwrap();
    assert_eq!(new["bot"][0]["engine_options"], old["bot"][0]["engine_options"]);
    assert_eq!(new["bot"][0]["provider_bot_ref"].as_str(), Some("worker-b"));
    assert_eq!(new["bot"][0]["token"].as_str(), Some(BOT_TOKEN));
    assert_eq!(new["api_url"].as_str(), Some(api.base.as_str()));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read_to_string(config_path(home.path())).unwrap(), original);
}

#[cfg(unix)]
#[tokio::test]
async fn replacement_registration_still_refuses_a_running_instance_before_network() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let args = ["--mode", "plugin", "--engine", "codex", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot", "--provider-bot-ref", "worker-a"];
    let initial = register(home.path(), &args).output().await.unwrap();
    assert!(initial.status.success(), "{:?}: {}", initial.status, diagnostic(&initial));
    let original = std::fs::read(config_path(home.path())).unwrap();
    let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
        .open(home.path().join(".bcn-bridge/bridge.lock")).unwrap();
    lock.try_lock().unwrap();
    let output = register(home.path(), &args).arg("--replace-existing").output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("bcs-bridge stop"), "{}", diagnostic(&output));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read(config_path(home.path())).unwrap(), original);
}

#[tokio::test]
async fn provider_admin_token_preserves_plain_registration_compatibility() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let output = register_as(home.path(), &["--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"], "provider_admin")
        .output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(saved(home.path())["registration"]["provider_auth"].as_str(), Some("static-bearer"));
}


#[tokio::test]
async fn explicit_resolver_overrides_token_selection_without_mode_comparison() {
    let home = tempfile::tempdir().unwrap();
    let api = Api::new().await;
    let output = register_as(home.path(), &["--provider-auth", "static-bearer", "--api-url", &api.base,
        "--upstream-url", "ws://127.0.0.1:21000/ws/bot"], "agentpass").output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(api.calls().len(), 1);
    for field in ["provider_auth", "auth_mode", "expected_auth_mode"] { assert!(!api.calls()[0].contains_key(field)); }
    assert_eq!(saved(home.path())["registration"]["provider_auth"].as_str(), Some("static-bearer"));
}

#[tokio::test]
async fn legacy_token_uses_explicit_or_default_resolver_and_reuses_profiles() {
    let mut bytes = URL_SAFE_NO_PAD.decode(token_with_auth("static_bearer")).unwrap();
    bytes.truncate(bytes.len() - 32);
    let mut payload: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    payload.as_object_mut().unwrap().remove("auth_mode");
    let mut bytes = serde_json::to_vec(&payload).unwrap();
    bytes.extend_from_slice(&[0; 32]);
    let legacy = URL_SAFE_NO_PAD.encode(bytes);
    for explicit in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let api = Api::new().await;
        let mut args = vec!["--profile", "legacy", "--api-url", &api.base,
            "--upstream-url", "ws://127.0.0.1:21000/ws/bot"];
        if explicit { args.extend(["--provider-auth", "static-bearer"]); }
        for _ in 0..2 {
            let output = register_with_token(home.path(), &args, &legacy).output().await.unwrap();
            assert!(output.status.success(), "{}", diagnostic(&output));
        }
        assert_eq!(api.calls().len(), 1, "the second registration must reuse the profile");
    }
}
