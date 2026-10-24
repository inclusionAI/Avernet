//! Registration must reject known failures before creating a remote Bot.
use std::{collections::HashMap, path::PathBuf, sync::{Arc, atomic::{AtomicUsize, Ordering}}};
use axum::{Router, extract::Query, http::StatusCode, routing::post};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;
use tokio::{net::TcpListener, process::Command};

struct Server { api: String, calls: Arc<AtomicUsize>, task: tokio::task::JoinHandle<()> }
impl Server {
    async fn new() -> Self {
        let calls = Arc::new(AtomicUsize::new(0)); let captured = calls.clone();
        let router = Router::new().route("/openapi/v1/collaboration/register", post(move |Query(q): Query<HashMap<String, String>>| {
            let calls = captured.clone(); async move {
                let count = calls.fetch_add(1, Ordering::SeqCst) + 1;
                let bytes = URL_SAFE_NO_PAD.decode(&q["token"]).unwrap();
                let claims: serde_json::Value = serde_json::from_slice(&bytes[..bytes.len()-32]).unwrap();
                let mut registration = json!({"provider_id":claims["provider_id"], "auth_mode":claims["auth_mode"],
                    "mode":"plugin", "provider_bot_ref":q["provider_bot_ref"]});
                if claims["auth_mode"] == "agentpass-subbots" {
                    let (parent, slug) = q["provider_bot_ref"].rsplit_once(':').unwrap();
                    registration["agent_code"] = parent.into(); registration["bot_slug"] = slug.into();
                }
                (StatusCode::CREATED, axum::Json(json!({"code":20100,"data":{
                    "bot_uuid":format!("new_bot_{count}"),"bot_token":"test-token","registration":registration}})))
            }
        }));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        Self { api, calls, task }
    }
    fn calls(&self) -> usize { self.calls.load(Ordering::SeqCst) }
}
impl Drop for Server { fn drop(&mut self) { self.task.abort(); } }

struct Fixture { home: tempfile::TempDir, config: PathBuf, engine: PathBuf }
impl Fixture {
    fn new(api: &str, counts: &[usize], legacy: bool, subbots: bool) -> Self {
        let home = tempfile::tempdir().unwrap();
        let directory = home.path().join(".bcn-bridge"); std::fs::create_dir(&directory).unwrap();
        let engine = home.path().join("mock-engine");
        std::fs::write(&engine, "#!/bin/sh\nexit 0\n").unwrap();
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&engine, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let engine = engine.canonicalize().unwrap();
        let mut text = if legacy { String::new() } else { "schema_version=2\n".into() };
        let prefix = if legacy { "" } else { "provider." };
        let auth = if subbots { "agentpass-subbots" } else { "agentpass" };
        for (p, count) in counts.iter().enumerate() {
            if !legacy { text.push_str("[[provider]]\n"); }
            text.push_str(&format!("provider_id='p{p}'\nprovider_auth='{auth}'\nmode='plugin'\napi_url='{api}'\nstate_path='p{p}.db'\n[{prefix}plugin]\nurl='ws://127.0.0.1:21000/ws/bot'\n"));
            std::fs::write(directory.join(format!("p{p}.db")), "retained hard state").unwrap();
            for b in 0..*count {
                let cwd = if subbots {
                    let cwd = home.path().join(format!("cwd-{p}-{b}")); std::fs::create_dir(&cwd).unwrap(); cwd
                } else { home.path().to_owned() };
                let reference = if subbots { format!("parent:worker-{b}") } else { format!("worker-{b}") };
                text.push_str(&format!("[[{prefix}bot]]\nprovider_bot_ref='{reference}'\nbot_id='bot_{p}_{b}'\ntoken='test-token'\nengine='codex'\nengine_bin='{}'\ncwd='{}'\n", engine.display(), cwd.display()));
                if subbots { text.push_str(&format!("agent_code='parent'\nbot_slug='worker-{b}'\n")); }
                text.push_str(&format!("[{prefix}bot.registration]\nprovider_auth='static-bearer'\napi_url='{api}'\n"));
            }
        }
        let config = directory.join("bridge.toml"); std::fs::write(&config, text).unwrap();
        Self { home, config, engine }
    }
    fn text(&self) -> String { std::fs::read_to_string(&self.config).unwrap() }
    fn document(&self) -> toml::Value { toml::from_str(&self.text()).unwrap() }
    fn save(&self, document: &toml::Value) { std::fs::write(&self.config, toml::to_string(document).unwrap()).unwrap(); }
    fn assert_no_recovery(&self) {
        assert!(!std::fs::read_dir(self.config.parent().unwrap()).unwrap().any(|entry|
            entry.unwrap().file_name().to_string_lossy().starts_with(".bridge-registration-")));
    }
    async fn register(&self, provider: &str, auth: &str, api: Option<&str>, extra: &[&str]) -> std::process::Output {
        let mut bytes = serde_json::to_vec(&json!({"v":2,"purpose":"provider_bot_registration","id":"human_test",
            "provider_id":provider,"auth_mode":auth,"allowed_modes":["plugin"],"exp":4102444800u64})).unwrap();
        bytes.extend_from_slice(&[0; 32]); let encoded = URL_SAFE_NO_PAD.encode(bytes);
        let mut command = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
        command.current_dir(self.home.path()).env("HOME", self.home.path())
            .env_remove("BRIDGE_CONFIG").env_remove("BRIDGE_REGISTER_TOKEN")
            .env("NO_PROXY", "127.0.0.1,localhost").env("no_proxy", "127.0.0.1,localhost")
            .args(["register", "--token", &encoded, "--bot-name", "Test", "--engine", "codex",
                "--engine-bin", self.engine.to_str().unwrap(), "--provider-auth", "static-bearer",
                "--upstream-url", "ws://127.0.0.1:21000/ws/bot"]);
        if !extra.contains(&"--bot-id") { command.args(["--provider-bot-ref", "parent"]); }
        if let Some(api) = api { command.args(["--api-url", api]); }
        command.args(extra).output().await.unwrap()
    }
}
fn success(output: &std::process::Output) {
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
}

#[tokio::test]
async fn capacity_overflow_never_registers_or_writes_recovery() {
    for (counts, legacy, subbots, provider, extra) in [
        (vec![1; 64], false, false, "new", vec![]),
        (vec![128, 128], false, false, "new", vec![]),
        (vec![128, 128], false, false, "p0", vec!["--add-bot"]),
        (vec![256], true, false, "new", vec![]),
        (vec![256], true, false, "p0", vec!["--add-bot"]),
        (vec![256], false, true, "p0", vec![]),
    ] {
        let server = Server::new().await;
        let fixture = Fixture::new(&server.api, &counts, legacy, subbots); let before = fixture.text();
        let auth = if subbots { "agentpass-subbots" } else { "agentpass" };
        let output = fixture.register(provider, auth, Some(&server.api), &extra).await;
        assert!(!output.status.success());
        assert_eq!(server.calls(), 0, "registration must not precede capacity validation");
        assert!(String::from_utf8_lossy(&output.stderr).contains("at most 64 Providers and 256 Bots"));
        assert_eq!(fixture.text(), before); fixture.assert_no_recovery();
        for p in 0..counts.len() {
            assert_eq!(std::fs::read(fixture.config.parent().unwrap().join(format!("p{p}.db"))).unwrap(), b"retained hard state");
        }
    }
}

#[tokio::test]
async fn registration_can_fill_the_last_provider_or_bot_slot() {
    for (counts, legacy, subbots, provider, extra, expected) in [
        (vec![1; 63], false, false, "new", vec![], (64, 64)),
        (vec![127, 128], false, false, "new", vec![], (3, 256)),
        (vec![127, 128], false, false, "p0", vec!["--add-bot"], (2, 256)),
        (vec![255], true, false, "new", vec![], (2, 256)),
        (vec![255], false, true, "p0", vec![], (1, 256)),
    ] {
        let server = Server::new().await;
        let fixture = Fixture::new(&server.api, &counts, legacy, subbots);
        let auth = if subbots { "agentpass-subbots" } else { "agentpass" };
        success(&fixture.register(provider, auth, Some(&server.api), &extra).await);
        assert_eq!(server.calls(), 1); fixture.assert_no_recovery();
        let document = fixture.document(); let groups = document["provider"].as_array().unwrap();
        assert_eq!((groups.len(), groups.iter().map(|g| g["bot"].as_array().unwrap().len()).sum::<usize>()), expected);
    }
}

#[tokio::test]
async fn reuse_and_replacement_remain_available_at_both_limits() {
    let server = Server::new().await;
    let fixture = Fixture::new(&server.api, &[4; 64], false, false);
    let before = fixture.text();
    success(&fixture.register("p0", "agentpass", None, &["--bot-id", "bot_0_1"]).await);
    assert_eq!(fixture.text(), before);
    success(&fixture.register("p0", "agentpass", None, &["--replace-existing", "--bot-id", "bot_0_1", "--model", "updated-model"]).await);
    let mut after = fixture.document();
    assert_eq!(after["provider"][0]["bot"][1].as_table_mut().unwrap().remove("model").unwrap().as_str(), Some("updated-model"));
    assert_eq!(after, toml::from_str::<toml::Value>(&before).unwrap());
    assert_eq!(server.calls(), 0); fixture.assert_no_recovery();
}

#[tokio::test]
async fn replacement_inherits_api_without_changing_siblings_or_binding() {
    for explicit_api in [false, true] {
        let server = Server::new().await;
        let fixture = Fixture::new(&server.api, &[2, 1], false, false);
        let mut before = fixture.document();
        before["provider"][0]["bot"][1]["registration"].as_table_mut().unwrap().remove("api_url");
        fixture.save(&before);
        let api = explicit_api.then_some(server.api.as_str());
        success(&fixture.register("p0", "agentpass", api, &["--replace-existing", "--bot-id", "bot_0_1", "--model", "updated-model"]).await);
        let mut after = fixture.document();
        assert_eq!(after["provider"][0]["bot"][1]["registration"]["api_url"].as_str(), Some(server.api.as_str()));
        after["provider"][0]["bot"][1]["registration"].as_table_mut().unwrap().remove("api_url");
        assert_eq!(after["provider"][0]["bot"][1].as_table_mut().unwrap().remove("model").unwrap().as_str(), Some("updated-model"));
        assert_eq!(after, before);
        assert_eq!(server.calls(), 0); fixture.assert_no_recovery();
        // Restoring inheritance must not permit an actual endpoint change.
        let saved = fixture.text();
        let output = fixture.register("p0", "agentpass", Some("http://127.0.0.1:9/different"),
            &["--replace-existing", "--bot-id", "bot_0_1", "--model", "another-model"]).await;
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("Replacement cannot change a Provider binding"));
        assert_eq!(fixture.text(), saved); assert_eq!(server.calls(), 0);
    }
}
