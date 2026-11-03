//! Real CLI + loopback registration; existing identities and DB files are hard state.
use std::{collections::HashMap, path::Path, sync::{Arc, Mutex}};
use axum::{Router, extract::Query, routing::post, http::StatusCode};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;
use tokio::{net::TcpListener, process::Command};

fn token(provider: &str, auth: &str) -> String {
    let mut bytes = serde_json::to_vec(&json!({"v":2,"purpose":"provider_bot_registration","id":"human_test",
        "provider_id":provider,"auth_mode":auth,"allowed_modes":["plugin"],"exp":4102444800u64})).unwrap();
    bytes.extend_from_slice(&[0; 32]); URL_SAFE_NO_PAD.encode(bytes)
}
fn command(home: &Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
    cmd.current_dir(home).env("HOME",home).env_remove("BRIDGE_CONFIG").env_remove("BRIDGE_REGISTER_TOKEN")
        .env("NO_PROXY","127.0.0.1,localhost").env("no_proxy","127.0.0.1,localhost");
    cmd
}
fn seed(home: &Path, api: &str) -> String {
    let dir = home.join(".bcn-bridge"); std::fs::create_dir_all(&dir).unwrap();
    std::fs::create_dir(home.join("legacy-cwd")).unwrap();
    // Own the executable fixture instead of assuming a platform-specific true path.
    let engine = home.join("mock-engine");
    std::fs::write(&engine, "#!/bin/sh\nexit 0\n").unwrap();
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&engine, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let engine = engine.canonicalize().unwrap();
    let text = format!(r#"# Handwritten legacy configuration; startup must not rewrite it.
provider_id='legacy'
provider_auth='agentpass'
api_url='{api}'
mode='plugin'
state_path='legacy.sqlite3'
[registration]
provider_auth='static-bearer'
api_url='{api}'
[plugin]
url='ws://127.0.0.1:21000/ws/bot'
[[bot]]
provider_bot_ref='parent'
bot_id='bot_legacy'
token='test-token'
engine='codex'
engine_bin='{}'
cwd='{}'
"#, engine.display(), home.join("legacy-cwd").display());
    std::fs::write(dir.join("bridge.toml"), &text).unwrap();
    let db = rusqlite::Connection::open(dir.join("legacy.sqlite3")).unwrap();
    db.execute_batch("CREATE TABLE retained (token TEXT); INSERT INTO retained VALUES ('rotated-legacy-token');").unwrap();
    text
}
fn read(home: &Path) -> String { std::fs::read_to_string(home.join(".bcn-bridge/bridge.toml")).unwrap() }
async fn register(home: &Path, api: &str, provider: &str, auth: &str, extra: &[&str]) -> std::process::Output {
    let engine = home.join("mock-engine").canonicalize().unwrap();
    let mut cmd=command(home); cmd.args(["register","--token",&token(provider,auth),"--bot-name","Test", "--engine","codex",
        "--engine-bin",engine.to_str().unwrap(),"--provider-auth","static-bearer","--api-url",api]);
    if !extra.contains(&"--bot-id") { cmd.args(["--provider-bot-ref","parent"]); }
    if !extra.contains(&"--upstream-url") { cmd.args(["--upstream-url","ws://127.0.0.1:21000/ws/bot"]); }
    cmd.args(extra).output().await.unwrap()
}
fn success(output: &std::process::Output) {
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains("test-token") && !stdout.contains("bot-token"));
}

#[tokio::test]
async fn legacy_plus_subbots_upgrade_once_and_preserve_all_old_hard_state() {
    let home = tempfile::tempdir().unwrap();
    let calls = Arc::new(Mutex::new(0usize)); let captured = calls.clone();
    let router = Router::new().route("/openapi/v1/collaboration/register",post(move |Query(q): Query<HashMap<String,String>>| {
        let calls = captured.clone(); async move {
            let mut count = calls.lock().unwrap(); *count += 1;
            let mut bytes = URL_SAFE_NO_PAD.decode(&q["token"]).unwrap(); bytes.truncate(bytes.len()-32);
            let claims: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
            let (parent,slug)=q["provider_bot_ref"].rsplit_once(':').unwrap();
            (StatusCode::CREATED, axum::Json(json!({"code":20100,"data":{"bot_uuid":format!("bot_new_{count}"),"bot_token":"bot-token",
                "registration":{"provider_id":claims["provider_id"],"auth_mode":claims["auth_mode"],"mode":"plugin",
                    "provider_bot_ref":q["provider_bot_ref"],"agent_code":parent,"bot_slug":slug}}})))
        }
    }));
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap(); let api=format!("http://{}",listener.local_addr().unwrap());
    let server=tokio::spawn(async move {axum::serve(listener,router).await.unwrap()});
    let old=seed(home.path(),&api);
    let dbpath=home.path().join(".bcn-bridge/legacy.sqlite3"); let db=std::fs::read(&dbpath).unwrap();
    let oldbot: toml::Value=toml::from_str(&old).unwrap();
    // Different custom endpoints must not imply a deployment mismatch.
    let upstream="ws://localhost:21001/ws/bot";
    success(&register(home.path(),&api,"multi","agentpass-subbots",&["--upstream-url",upstream]).await);
    let document:toml::Value=toml::from_str(&read(home.path())).unwrap();
    assert_eq!(document["schema_version"].as_integer(),Some(2));
    let groups=document["provider"].as_array().unwrap(); assert_eq!(groups.len(),2);
    assert_eq!(groups[0]["plugin"],oldbot["plugin"]);
    assert_eq!(groups[1]["plugin"]["url"].as_str(),Some(upstream));
    assert_eq!(groups[1]["bot"][0]["engine_bin"],oldbot["bot"][0]["engine_bin"]);
    for key in ["provider_bot_ref","bot_id","token","cwd","engine_bin"] { assert_eq!(groups[0]["bot"][0][key],oldbot["bot"][0][key]); }
    assert_eq!(groups[0]["provider_auth"].as_str(),Some("agentpass"));
    assert_eq!(groups[0]["bot"][0]["registration"]["provider_auth"].as_str(),Some("static-bearer"));
    assert_eq!(groups[0]["state_path"].as_str(),Some("legacy.sqlite3"));
    assert_ne!(groups[0]["state_path"],groups[1]["state_path"]);
    assert_eq!(std::fs::read(&dbpath).unwrap(),db);
    let backups:Vec<_>=std::fs::read_dir(home.path().join(".bcn-bridge")).unwrap().filter_map(|e| {
        let path=e.unwrap().path(); path.file_name().unwrap().to_string_lossy().starts_with(".bridge-backup-").then_some(path)
    }).collect();
    assert_eq!(backups.len(),1); assert_eq!(std::fs::read_to_string(&backups[0]).unwrap(),old);
    let old_group=groups[0].clone(); let first_new=groups[1]["bot"][0].clone();
    success(&register(home.path(),&api,"multi","agentpass-subbots",&["--upstream-url",upstream]).await);
    let document:toml::Value=toml::from_str(&read(home.path())).unwrap();
    assert_eq!(document["provider"][0],old_group);
    assert_eq!(document["provider"][1]["bot"][0],first_new);
    assert_eq!(document["provider"][1]["bot"].as_array().unwrap().len(),2);
    assert_eq!(*calls.lock().unwrap(),2);
    let before=read(home.path());
    // A selector must never accidentally create another subbot.
    assert!(!register(home.path(),&api,"multi","agentpass-subbots",&["--bot-id","missing"]).await.status.success());
    assert_eq!(read(home.path()),before); assert_eq!(*calls.lock().unwrap(),2);
    // Explicit reuse is a no-op, including per-Bot resolver metadata.
    success(&register(home.path(),&api,"multi","agentpass-subbots",&["--bot-id","bot_new_1","--upstream-url",upstream]).await);
    assert_eq!(read(home.path()),before);
    assert_eq!(*calls.lock().unwrap(),2);
    server.abort();
}

#[tokio::test]
async fn running_lock_refuses_upgrade_before_network() {
    let home=tempfile::tempdir().unwrap(); let before=seed(home.path(),"http://127.0.0.1:9");
    let lock=std::fs::File::create(home.path().join(".bcn-bridge/bridge.lock")).unwrap(); lock.try_lock().unwrap();
    let out=register(home.path(),"http://127.0.0.1:9","multi","agentpass-subbots",&[]).await;
    assert!(!out.status.success());
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("stop"), "{stderr}");
    assert_eq!(read(home.path()),before);
}
