#![cfg(unix)]
use std::{path::PathBuf, sync::Arc};
use bcs_bridge_core::config::ProviderConfig;
use bcs_bridge_core::engine::{claude_code::ClaudeCodeFactory, Engine, EngineFactory, SessionObserver, TurnRequest};

struct Observer;
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self,_:&str)->Result<(),String>{Ok(())}
}

#[tokio::test]
async fn concurrent_engine_processes_receive_their_own_actor() {
    use std::os::unix::fs::PermissionsExt;
    let dir=tempfile::tempdir().unwrap();let bin=dir.path().join("engine");
    std::fs::write(&bin,r#"#!/bin/sh
# Consume the initial request before replying so the driver cannot write to a closed pipe.
IFS= read -r request || exit 1
printf '{"type":"result","subtype":"success","result":"%s","is_error":false,"session_id":"session"}\n' "$BCN_ACTOR_BOT_ID|$BCN_BOT_TOKEN|$BCS_API_BASE_URL|$MOLTIS_BCS_URL"
"#).unwrap();
    std::fs::set_permissions(&bin,std::fs::Permissions::from_mode(0o755)).unwrap();
    // The shell peer only implements legacy result frames; isolate caller env here.
    let options = toml::toml! { completion_mode = "legacy" };
    let engine = ClaudeCodeFactory::default().build(bin, &options).unwrap();
    async fn run(engine: Arc<dyn Engine>,cwd:PathBuf,actor:&str)->String {
        let mut req=TurnRequest::new(actor,"hello",cwd,Arc::new(Observer));
        let config = ProviderConfig::from_toml(&format!(r#"
provider_id = "p"
listen = "127.0.0.1:0"
bcs_to_provider_token = "webhook"
api_url = "https://registration.example"
[[bot]]
provider_bot_ref = "{actor}"
bot_id = "{actor}"
token = "token-{actor}"
engine = "claude-code"
cwd = "/tmp"
"#), std::path::Path::new("/tmp/bridge.toml")).unwrap();
        let mut config = config;
        config.cli_environment.insert("BCS_API_BASE_URL".into(), "https://gateway.example/cli".into());
        req.env=config.execution_env(&config.bots[0]);
        let(tx,mut rx)=tokio::sync::mpsc::channel(64);
        let consumer=tokio::spawn(async move{while rx.recv().await.is_some(){}});
        let outcome=engine.run_turn(req,tx,tokio_util::sync::CancellationToken::new()).await.unwrap();
        consumer.await.unwrap();outcome.final_text.unwrap()
    }
    let (a,b)=tokio::join!(run(engine.clone(),dir.path().into(),"bot_A"),run(engine,dir.path().into(),"bot_B"));
    assert_eq!(a,"bot_A|token-bot_A|https://gateway.example/cli|https://gateway.example/cli");
    assert_eq!(b,"bot_B|token-bot_B|https://gateway.example/cli|https://gateway.example/cli");
}

#[test]
fn legacy_engine_removes_inherited_actor_in_an_isolated_process() {
    let output=std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact","legacy_environment_child","--nocapture"])
        .env("BRIDGE_ENV_CHILD","1").env("BCN_AUTH_MODE","agentpass-subbots")
        .env("BCN_ACTOR_BOT_ID","wrong-parent-shell-bot").env("BRIDGE_TEST_UNRELATED","retained")
        .output().unwrap();
    assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stdout));
}

#[tokio::test]
async fn legacy_environment_child() {
    if std::env::var("BRIDGE_ENV_CHILD").as_deref()!=Ok("1") { return; }
    use std::os::unix::fs::PermissionsExt;
    let dir=tempfile::tempdir().unwrap(); let bin=dir.path().join("engine");
    std::fs::write(&bin,r#"#!/bin/sh
IFS= read -r request || exit 1
printf '{"type":"result","subtype":"success","result":"%s|%s|%s","is_error":false,"session_id":"session"}\n' "$BCN_AUTH_MODE" "$BCN_ACTOR_BOT_ID" "$BRIDGE_TEST_UNRELATED"
"#).unwrap();
    std::fs::set_permissions(&bin,std::fs::Permissions::from_mode(0o755)).unwrap();
    let options = toml::toml! { completion_mode = "legacy" };
    let engine=ClaudeCodeFactory::default().build(bin,&options).unwrap();
    let req=TurnRequest::new("legacy","hello",dir.path(),Arc::new(Observer));
    let(tx,mut rx)=tokio::sync::mpsc::channel(64);
    let consumer=tokio::spawn(async move{while rx.recv().await.is_some(){}});
    let outcome=engine.run_turn(req,tx,tokio_util::sync::CancellationToken::new()).await.unwrap();
    consumer.await.unwrap();
    assert_eq!(outcome.final_text.as_deref(),Some("||retained"));
    assert_eq!(std::env::var("BCN_ACTOR_BOT_ID").unwrap(),"wrong-parent-shell-bot");
}
